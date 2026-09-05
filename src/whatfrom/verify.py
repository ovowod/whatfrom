from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatfrom.contracts import Candidate, Recommendation
from whatfrom.models import ImageTag


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str | None = None
    # 후보에 없는 대안들. 답변을 폐기하는 대신 이것만 떼어낸다.
    dropped_alternatives: tuple[str, ...] = ()


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

    return VerifyResult(
        True, dropped_alternatives=tuple(a for a in rec.alternatives if not is_real(a))
    )
