# src/whatfrom/eval/scoring.py
from dataclasses import dataclass

from whatfrom.core.contracts import Candidate, Platform, RecommendResponse
from whatfrom.core.versions import extends_version
from whatfrom.eval.goldenset import Conditions, GoldenCase


@dataclass(frozen=True)
class RetrievalScore:
    """LLM 없이 계산되는 지표들. 전체 모드의 CaseScore에도 그대로 들어간다."""

    case_id: str
    candidate_hit: bool
    # 무작위 선택 대조군과 사후 분석에 쓴다. 결과 JSON에 후보가 없으면
    # 같은 DB와 임베딩 모델로 검색을 다시 돌려야 알 수 있다.
    candidate_count: int
    accepted_count: int
    candidate_images: list[str]
    hit_declared: bool
    hit_at5: bool


def _is_accepted(case: GoldenCase, candidate: Candidate, accepted_digests: frozenset[str]) -> bool:
    """후보가 정답과 같은 이미지인가.

    이름이 accept에 있거나, digest가 accept 태그의 digest와 같으면 같은 이미지다.
    eclipse-temurin:25-noble과 25-jdk-noble처럼 이름만 다른 별칭을 accept에 전부
    적을 수는 없다. 다만 이름이 reject에 있으면 digest가 같아도 오답이다.
    postgres:latest는 지금 18과 같은 이미지지만 메이저가 바뀔 수 있어 오답으로 지정했다.
    digest는 모든 플랫폼을 묶은 index의 digest라 같으면 모든 아키텍처에서 같다.
    """
    if candidate.image in case.reject:
        return False
    if candidate.image in case.accept:
        return True
    return candidate.digest is not None and candidate.digest in accepted_digests


def _candidate_hit(
    case: GoldenCase, candidates: list[Candidate], accepted_digests: frozenset[str]
) -> bool:
    """허용 집합 중 하나라도 후보에 있는가.

    추천은 후보 안에서만 선택하므로 후보에 정답이 없으면 최종 추천도 맞출 수 없다.
    이 값을 추천 정확도와 비교하면 후보 검색과 최종 선택 중 어느 단계에서
    정답을 놓쳤는지 파악하는 데 도움이 된다. 정확도와 같은 판정을 써야 상한이 된다.
    """
    return any(_is_accepted(case, c, accepted_digests) for c in candidates)


def _hit_at5(case: GoldenCase, sections: list[tuple[str, str]]) -> bool:
    """필요한 리포지터리의 검색 결과에 기대 섹션 제목이 하나라도 포함되는지 확인한다.

    호출자가 상위 5개 청크의 (리포지터리, 섹션 제목)을 전달해야 한다.
    다른 제품의 동명 섹션은 제외하고, 버전 등이 붙은 제목도 찾도록 부분 일치한다.
    기대 섹션 하나만 발견해도 성공이므로 관련 문서의 회수 비율인 Recall과 다르다.
    제목만 비교하므로 청크 본문이 질문의 근거인지는 검증하지 않는다.
    """
    wanted = set(case.requires_repositories)
    return any(
        expected in title
        for expected in case.expected_sections
        for repository, title in sections
        if repository in wanted
    )


def score_retrieval(
    case: GoldenCase,
    candidates: list[Candidate],
    sections: list[tuple[str, str]],
    accepted_digests: frozenset[str] = frozenset(),
) -> RetrievalScore:
    """sections는 상위 청크의 (리포, 섹션 제목) 쌍이다. 러너가 문서에서 뽑아 넘긴다.

    accepted_digests는 accept 태그들의 digest다. 러너가 DB에서 조회해 넘긴다.
    """
    declared = bool(case.expected_sections)
    images = [c.image for c in candidates]
    return RetrievalScore(
        case_id=case.id,
        candidate_hit=_candidate_hit(case, candidates, accepted_digests),
        candidate_count=len(images),
        accepted_count=sum(_is_accepted(case, c, accepted_digests) for c in candidates),
        candidate_images=images,
        hit_declared=declared,
        hit_at5=declared and _hit_at5(case, sections),
    )


MB = 1_000_000


def _linux_platforms(candidate: Candidate, architecture: str | None = None) -> list[Platform]:
    """Linux 플랫폼을 선택하고, 아키텍처가 지정되면 해당 항목만 남긴다.

    같은 태그·아키텍처에 Windows 이미지도 있을 수 있으므로 OS를 함께 검사한다.
    현재 평가의 아키텍처·크기 조건은 Linux 이미지를 기준으로 한다.
    """
    return [
        p
        for p in candidate.platforms
        if p.os == "linux" and (architecture is None or p.architecture == architecture)
    ]


def conditions_satisfied(conditions: Conditions, candidate: Candidate) -> bool:
    """추천 이미지가 선언된 조건을 전부 만족하는가.

    버전과 배포판은 후보의 파생 값으로 판정한다. python:3.14처럼 이름에 배포판이
    없는 태그도 같은 이미지의 3.14-trixie에서 물려받은 값으로 판정된다.
    파생 값이 없으면(수집 직후 실패, 충돌, 오래된 태그) 태그 이름으로 판정한다.
    """
    for architecture in conditions.architectures:
        if not _linux_platforms(candidate, architecture):
            return False

    # 골든셋은 배포판(alpine)과 코드네임(trixie)을 둘 다 제외 값으로 쓴다.
    derived = {candidate.distribution, candidate.distro_codename} - {None}
    for excluded in conditions.exclude_distributions:
        if (excluded in derived) if derived else (excluded in candidate.tag):
            return False

    if conditions.version_prefix is not None:
        # "2"가 "20-alpine"과 일치하지 않도록 버전 경계를 확인한다. 경계는 별칭 해석과 같다.
        # 8-jdk의 파생 버전 8u502-b07도 "8"로 시작하는 것으로 본다.
        if not extends_version(conditions.version_prefix, candidate.version or candidate.tag):
            return False

    if conditions.max_size_mb is not None:
        # 현재는 첫 번째 요구 아키텍처만 검사하며, 미지정 시 amd64를 사용한다.
        # 같은 아키텍처의 플랫폼이 여럿이면 가장 작은 이미지의 크기로 판정한다.
        architecture = conditions.architectures[0] if conditions.architectures else "amd64"
        platforms = _linux_platforms(candidate, architecture)
        if not platforms:
            return False
        if min(p.size_bytes for p in platforms) > conditions.max_size_mb * MB:
            return False

    return True


@dataclass(frozen=True)
class CaseScore:
    """문항 하나의 전체 채점 결과. 지표 6종이 이 레코드에서 집계된다."""

    case_id: str
    recommended_image: str | None
    accurate: bool
    # 이름은 accept에 없지만 digest가 같아 정답으로 인정했는가.
    accurate_by_digest: bool
    # 지표가 아니라 경보다. 리포트의 실패 목록에 표시된다.
    rejected_pick: bool
    candidate_hit: bool
    candidate_count: int
    accepted_count: int
    candidate_images: list[str]
    # 추천이 없으면 None. 분모에서 빠진다.
    tag_real: bool | None
    conditions_declared: bool
    conditions_met: bool
    sources_ok: bool
    hit_declared: bool
    hit_at5: bool
    degraded_note: str | None


def _sources_ok(response: RecommendResponse) -> bool:
    """출처 URL과 수집 시점이 갖춰졌는가.

    추천이 있으면 그 이미지에 대응하는 후보를 본다. 추천이 없으면 반환된 후보
    전부를 본다 — 저하되어 표만 받은 사용자도 출처는 받은 것이기 때문이다.
    """
    if response.recommendation is not None:
        targets = [c for c in response.candidates if c.image == response.recommendation.image]
    else:
        targets = list(response.candidates)

    if not targets:
        return False
    return all(bool(c.source_url) and c.collected_at is not None for c in targets)


def score_full(
    case: GoldenCase,
    response: RecommendResponse,
    sections: list[tuple[str, str]],
    recommended_image_exists: bool | None,
    accepted_digests: frozenset[str] = frozenset(),
) -> CaseScore:
    """지표 6종을 한 문항에 대해 채점한다.

    recommended_image_exists는 러너가 DB에서 조회해 넘긴다. 응답만 보고 판정하면
    verify가 통과시킨 것을 그대로 다시 믿는 셈이라 불변식(스펙 §2)을 독립적으로
    검증하지 못한다. 추천이 없으면 None이고 태그 실재율 분모에서 빠진다.
    """
    retrieval = score_retrieval(case, response.candidates, sections, accepted_digests)
    recommendation = response.recommendation
    image = recommendation.image if recommendation is not None else None

    recommended = (
        next((c for c in response.candidates if c.image == image), None)
        if image is not None
        else None
    )

    by_name = image is not None and image in case.accept
    accurate = by_name or (
        recommended is not None and _is_accepted(case, recommended, accepted_digests)
    )

    return CaseScore(
        case_id=case.id,
        recommended_image=image,
        accurate=accurate,
        accurate_by_digest=accurate and not by_name,
        rejected_pick=image is not None and image in case.reject,
        candidate_hit=retrieval.candidate_hit,
        candidate_count=retrieval.candidate_count,
        accepted_count=retrieval.accepted_count,
        candidate_images=retrieval.candidate_images,
        # 추천이 없으면 호출자가 무엇을 넘겼든 None이다. 답하지 않은 것을
        # "환각하지 않았음"으로 세면 저하가 잦을수록 점수가 오른다.
        tag_real=recommended_image_exists if image is not None else None,
        conditions_declared=case.conditions.declared,
        # 추천이 없으면 불일치로 센다. 분모에서 빼면 저하가 잦을수록 점수가 오른다.
        conditions_met=(
            case.conditions.declared
            and recommended is not None
            and conditions_satisfied(case.conditions, recommended)
        ),
        sources_ok=_sources_ok(response),
        hit_declared=retrieval.hit_declared,
        hit_at5=retrieval.hit_at5,
        degraded_note=response.notes[0] if recommendation is None and response.notes else None,
    )
