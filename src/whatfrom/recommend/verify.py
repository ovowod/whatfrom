import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatfrom.core.contracts import Candidate, Recommendation
from whatfrom.core.models import ImageTag

# FROM [--platform=...] <ref> [AS <stage>]
# Dockerfile 명령은 대소문자를 구분하지 않는다. 대문자만 보면 `from unknown:tag` 줄이
# 검증을 빠져나가 없는 이미지가 Dockerfile로 사용자에게 보인다.
# kw는 FROM 키워드 자체의 위치다. `\s*`가 줄바꿈을 건너뛸 수 있어 match 시작과 다를 수 있다.
_FROM = re.compile(
    r"^\s*(?P<kw>FROM)\s+(?:--\S+\s+)*(?P<ref>\S+)(?:\s+AS\s+(?P<alias>\S+))?",
    re.MULTILINE | re.IGNORECASE,
)
# 대문자 FROM은 main과 같이 위치와 무관하게 무조건 받아들인다. 소문자/대소문자 섞인 줄은
# 줄 전체가 `FROM [--flag...] 참조 [AS 이름]` 문법에 맞을 때만 받아들인다 — Docker는 실제
# 명령인 줄에만 이 문법을 강제하고(안 맞으면 빌드 에러), 이음·heredoc 줄은 애초에 명령이
# 아니라 검사 대상이 아니다. ref·alias도 이 줄 안에서만 다시 찾는다: `_FROM`의 `\s+`는
# 줄바꿈을 건너뛸 수 있어 관대한 매치의 ref·alias가 다음 줄까지 삼킬 수 있기 때문이다.
_FROM_LINE = re.compile(
    r"[^\S\n]*FROM[^\S\n]+(?:--\S+[^\S\n]+)*(?P<ref>\S+)"
    r"(?:[^\S\n]+AS[^\S\n]+(?P<alias>\S+))?[^\S\n]*(?:\\[^\S\n]*)?",
    re.IGNORECASE,
)


def image_ref_spans(dockerfile: str) -> list[tuple[str, tuple[int, int]]]:
    """FROM이 가져오는 이미지 참조와 그 위치. 검증과 digest 고정이 함께 쓰는 파서다.

    멀티스테이지에서 앞 단계 이름을 참조하는 FROM과 `scratch`는 이미지가 아니므로
    제외한다. 나머지는 전부 실재해야 하는 이미지 참조다.
    """
    stages: set[str] = set()
    spans: list[tuple[str, tuple[int, int]]] = []
    pos = 0
    while match := _FROM.search(dockerfile, pos):
        if match.group("kw") == "FROM":
            ref, alias = match.group("ref"), match.group("alias")
            span = match.span("ref")
            pos = match.end()
        else:
            # 소문자/혼합 줄은 해당 줄 하나로만 다시 판단한다 — 받아들이든 거부하든 다음
            # 검색은 이 줄 끝부터 재개해, 뒤에 오는 진짜 FROM을 놓치거나 삼키지 않는다.
            kw_start = match.start("kw")
            line_start = dockerfile.rfind("\n", 0, kw_start) + 1
            line_end = dockerfile.find("\n", kw_start)
            if line_end == -1:
                line_end = len(dockerfile)
            line = dockerfile[line_start:line_end].removesuffix("\r")
            pos = line_end
            line_match = _FROM_LINE.fullmatch(line)
            if not line_match:
                continue
            ref, alias = line_match.group("ref"), line_match.group("alias")
            span = (line_start + line_match.start("ref"), line_start + line_match.end("ref"))
        if ref.lower() != "scratch" and ref not in stages:
            spans.append((ref, span))
        if alias:
            stages.add(alias)
    return spans


def dockerfile_image_refs(dockerfile: str) -> list[str]:
    """Dockerfile이 FROM으로 가져오는 이미지들."""
    return [ref for ref, _ in image_ref_spans(dockerfile)]


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str | None = None
    # 후보에 없는 대안들. 답변을 폐기하는 대신 이것만 떼어낸다.
    dropped_alternatives: tuple[str, ...] = ()
    # Dockerfile의 FROM이 가리키는 것 중 추천/대안 어느 것도 아닌 참조.
    unverifiable_dockerfile_refs: tuple[str, ...] = ()


def verify_recommendation(
    session: Session, rec: Recommendation, candidates: list[Candidate]
) -> VerifyResult:
    """추천 이미지가 (1) 우리가 준 후보 중 하나이고 (2) DB에 실재하는지 검사한다.

    지키려는 것은 "존재하지 않는 이미지가 사용자에게 보이지 않는다"이지 "LLM이 완벽하다"가 아니다.

    그래서 대응이 갈린다:

    - `rec.image`가 어긋나면 답변 전체를 폐기한다 (ok=False). 추천 자체가 무의미하다.
    - `alternatives`가 어긋나면 그것만 떼어낸다. 실측에서 모델이 대안에
      "python:3.13-alpine(호환성 문제 가능성)"처럼 주석을 덧붙이는 일이 흔했는데,
      멀쩡한 주 추천까지 버리는 건 과한 처벌이고 저하를 상시 발동시킨다.
    """
    offered = {c.image for c in candidates}

    def is_real(image: str) -> bool:
        if image.count(":") != 1 or image.startswith(":") or image.endswith(":"):
            return False
        if image not in offered:
            return False
        repository, tag = image.split(":", 1)
        return (
            session.execute(
                select(ImageTag.id).where(ImageTag.repository == repository, ImageTag.tag == tag)
            ).scalar_one_or_none()
            is not None
        )

    if not is_real(rec.image):
        return VerifyResult(False, f"{rec.image} is not a verifiable candidate image")

    dropped = tuple(a for a in rec.alternatives if not is_real(a))

    # Dockerfile의 FROM도 사용자에게 이미지 이름으로 노출된다. 오히려 복사해서
    # 그대로 쓰는 쪽이라 image 필드보다 더 위험하다. 살아남은 추천·대안만
    # 가리킬 수 있게 한다 — 실재하더라도 자기가 추천하지 않은 이미지를 쓰면
    # 답변이 자기모순이다.
    allowed = {rec.image} | {a for a in rec.alternatives if a not in dropped}
    bad_refs = tuple(ref for ref in dockerfile_image_refs(rec.dockerfile) if ref not in allowed)

    return VerifyResult(True, dropped_alternatives=dropped, unverifiable_dockerfile_refs=bad_refs)
