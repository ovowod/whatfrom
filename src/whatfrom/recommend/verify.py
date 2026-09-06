import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatfrom.core.contracts import Candidate, Recommendation
from whatfrom.core.models import ImageTag

# FROM [--platform=...] <ref> [AS <stage>]
_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+[Aa][Ss]\s+(\S+))?", re.MULTILINE)


def dockerfile_image_refs(dockerfile: str) -> list[str]:
    """Dockerfile이 FROM으로 가져오는 이미지들.

    멀티스테이지에서 앞 단계 이름을 참조하는 FROM과 `scratch`는 이미지가 아니므로
    제외한다. 나머지는 전부 실재해야 하는 이미지 참조다.
    """
    stages: set[str] = set()
    refs: list[str] = []
    for match in _FROM.finditer(dockerfile):
        ref, alias = match.group(1), match.group(2)
        if ref.lower() != "scratch" and ref not in stages:
            refs.append(ref)
        if alias:
            stages.add(alias)
    return refs


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
