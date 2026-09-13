# src/whatfrom/eval/scoring.py
from dataclasses import dataclass

from whatfrom.core.contracts import Candidate, Platform, RecommendResponse
from whatfrom.eval.goldenset import Conditions, GoldenCase


@dataclass(frozen=True)
class RetrievalScore:
    """LLM 없이 계산되는 지표들. 전체 모드의 CaseScore에도 그대로 들어간다."""

    case_id: str
    candidate_hit: bool
    hit_declared: bool
    hit_at5: bool


def _candidate_hit(case: GoldenCase, candidates: list[Candidate]) -> bool:
    """허용 집합 중 하나라도 후보에 있는가.

    추천은 후보 안에서만 선택하므로 후보에 정답이 없으면 최종 추천도 맞출 수 없다.
    이 값을 추천 정확도와 비교하면 후보 검색과 최종 선택 중 어느 단계에서
    정답을 놓쳤는지 파악하는 데 도움이 된다.
    """
    offered = {c.image for c in candidates}
    return any(image in offered for image in case.accept)


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
    case: GoldenCase, candidates: list[Candidate], sections: list[tuple[str, str]]
) -> RetrievalScore:
    """sections는 상위 청크의 (리포, 섹션 제목) 쌍이다. 러너가 문서에서 뽑아 넘긴다."""
    declared = bool(case.expected_sections)
    return RetrievalScore(
        case_id=case.id,
        candidate_hit=_candidate_hit(case, candidates),
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

    버전과 배포판은 현재 태그 문자열로만 판정한다. 별칭이 가리키는 실제 버전이나
    태그에 드러나지 않는 배포판은 판별하지 못한다. 이를 판별하려면 후보에
    정규화된 이미지 메타데이터를 전달하도록 확장해야 한다.
    """
    for architecture in conditions.architectures:
        if not _linux_platforms(candidate, architecture):
            return False

    # 제한: 배포판 문자열이 없는 태그는 실제 배포판과 무관하게 통과한다.
    # 예를 들어 "3.13"만으로는 Debian 제외 조건을 검증할 수 없다.
    for distribution in conditions.exclude_distributions:
        if distribution in candidate.tag:
            return False

    if conditions.version_prefix is not None:
        # "2"가 "20-alpine"과 일치하지 않도록 버전 경계를 확인한다.
        # 태그 전체가 접두사와 같거나, 접두사 다음에 "." 또는 "-"가 있어야 한다.
        prefix = conditions.version_prefix
        tag = candidate.tag
        if not (tag == prefix or tag.startswith(prefix + ".") or tag.startswith(prefix + "-")):
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
    # 지표가 아니라 경보다. 리포트의 실패 목록에 표시된다.
    rejected_pick: bool
    candidate_hit: bool
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
) -> CaseScore:
    """지표 6종을 한 문항에 대해 채점한다.

    recommended_image_exists는 러너가 DB에서 조회해 넘긴다. 응답만 보고 판정하면
    verify가 통과시킨 것을 그대로 다시 믿는 셈이라 불변식(스펙 §2)을 독립적으로
    검증하지 못한다. 추천이 없으면 None이고 태그 실재율 분모에서 빠진다.
    """
    retrieval = score_retrieval(case, response.candidates, sections)
    recommendation = response.recommendation
    image = recommendation.image if recommendation is not None else None

    recommended = (
        next((c for c in response.candidates if c.image == image), None)
        if image is not None
        else None
    )

    return CaseScore(
        case_id=case.id,
        recommended_image=image,
        accurate=image is not None and image in case.accept,
        rejected_pick=image is not None and image in case.reject,
        candidate_hit=retrieval.candidate_hit,
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
