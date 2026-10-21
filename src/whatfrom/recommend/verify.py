from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatfrom.core.contracts import (
    Candidate,
    CheckedClaim,
    CheckedRecommendation,
    Citation,
    CitationCheck,
    NumberedEvidence,
    Recommendation,
    split_image,
)
from whatfrom.core.models import ImageTag


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str | None = None
    # 후보에 없는 대안들. 답변을 폐기하는 대신 이것만 떼어낸다.
    dropped_alternatives: tuple[str, ...] = ()


def image_exists(session: Session, image: str) -> bool:
    """image("repository:tag")가 수집된 tag인가. 요청 시점에 받을 수 있는지까지는 보지 않는다."""
    parts = split_image(image)
    if parts is None:
        return False
    repository, tag = parts
    return (
        session.execute(
            select(ImageTag.id).where(ImageTag.repository == repository, ImageTag.tag == tag)
        ).scalar_one_or_none()
        is not None
    )


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
        return image in offered and image_exists(session, image)

    if not is_real(rec.image):
        return VerifyResult(False, f"{rec.image} is not a verifiable candidate image")

    dropped = tuple(a for a in rec.alternatives if not is_real(a))
    return VerifyResult(True, dropped_alternatives=dropped)


def _normalized(text: str) -> str:
    """연속 공백(줄바꿈 포함)을 공백 하나로. README의 줄바꿈과 인용의 줄바꿈이 다를 수 있다."""
    return " ".join(text.split())


def verify_citation(
    citation: Citation, evidence: Mapping[int, NumberedEvidence], repositories: set[str]
) -> str | None:
    """근거 인용의 진위를 본다. 통과하면 None, 실패하면 그 이유다(ADR 0003).

    DB가 아니라 그 요청에서 LLM에 넘긴 근거 본문으로 본다. 인용이 주장을 뒷받침하는지는
    보지 않는다. 그것은 평가에서 잰다.
    repositories는 인용이 뒷받침할 수 있는 repository다. 추천 이미지와 검증된 대안의 것이다.
    """
    source = evidence.get(citation.evidence)
    if source is None:
        return "unknown evidence"
    quote = _normalized(citation.quote)
    if not quote:
        return "empty quote"
    if quote not in _normalized(source.content):
        return "quote not in evidence"
    if source.repository not in repositories:
        return "evidence from another repository"
    return None


def check_citations(
    rec: Recommendation, evidence: list[NumberedEvidence]
) -> tuple[CheckedRecommendation, int]:
    """실재성 검증을 통과한 추천의 인용마다 검증 결과를 붙인다. (응답의 추천, 실패한 인용 수).

    인용이 실패해도 추천과 주장은 버리지 않는다. 받는 쪽이 무엇이 검증되지 않았는지 본다.
    """
    by_number = {item.number: item for item in evidence}
    repositories = {
        parts[0] for image in [rec.image, *rec.alternatives] if (parts := split_image(image))
    }
    claims: list[CheckedClaim] = []
    verified = failed = 0
    for claim in rec.claims:
        checks: list[CitationCheck] = []
        for citation in claim.citations:
            problem = verify_citation(citation, by_number, repositories)
            verified += problem is None
            failed += problem is not None
            checks.append(
                CitationCheck(**citation.model_dump(), verified=problem is None, problem=problem)
            )
        claims.append(CheckedClaim(text=claim.text, citations=checks))
    checked = CheckedRecommendation(
        image=rec.image, alternatives=rec.alternatives, claims=claims, verified_citations=verified
    )
    return checked, failed
