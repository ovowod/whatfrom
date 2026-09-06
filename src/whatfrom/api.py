# src/whatfrom/api.py
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

from fastapi import FastAPI
from pydantic import BaseModel, field_validator
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from whatfrom.advisor import advise
from whatfrom.config import settings
from whatfrom.contracts import RecommendResponse
from whatfrom.db import make_engine
from whatfrom.embed import Embedder, get_embedder
from whatfrom.httpclient import RemoteCallError
from whatfrom.llm import LLMProvider, get_provider
from whatfrom.retrieval import search_candidates_by_vector
from whatfrom.verify import verify_recommendation


class RecommendRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


def recommend_for_question(
    open_session: Callable[[], AbstractContextManager[Session]],
    embedder: Embedder,
    provider: LLMProvider,
    question: str,
) -> RecommendResponse:
    """성능 저하 사다리 (스펙 §8). 어느 단계에서 멈추든 200으로 답한다.

    세션 하나가 아니라 세션 팩토리를 받는다. 이 경로에는 외부 HTTP 호출이 둘
    있고(임베딩, LLM) 둘 다 수 초가 걸릴 수 있는데, 한 세션으로 묶으면 그동안
    DB 커넥션이 트랜잭션을 연 채로 묶인다. 동시 요청이 조금만 늘어도 LLM
    용량보다 커넥션 풀이 먼저 마른다 — collector에서 이미 한 번 고친 결함이다.
    그래서 DB를 만지는 구간만 짧게 열고, 외부 호출은 그 밖에서 한다.
    """
    notes: list[str] = []

    # 임베딩도 HTTP 호출이라 실패한다. 매 요청마다 부르므로 LLM보다 자주 실패한다.
    try:
        vector = embedder.embed([question])[0]
    except RemoteCallError as exc:
        notes.append(f"임베딩 생성에 실패해 후보를 만들지 못했습니다: {exc}")
        return RecommendResponse(
            question=question, recommendation=None, candidates=[], degraded=True, notes=notes
        )

    with open_session() as session:
        candidates = search_candidates_by_vector(session, vector)

    if not candidates:
        notes.append("검색된 후보가 없습니다. 수집·인덱싱이 되어 있는지 확인하세요.")
        return RecommendResponse(
            question=question, recommendation=None, candidates=[], degraded=True, notes=notes
        )

    try:
        recommendation = advise(provider, question, candidates)
    except RemoteCallError as exc:
        notes.append(f"LLM 근거 생성에 실패해 후보 목록만 반환합니다: {exc}")
        return RecommendResponse(
            question=question,
            recommendation=None,
            candidates=candidates,
            degraded=True,
            notes=notes,
        )

    with open_session() as session:
        verdict = verify_recommendation(session, recommendation, candidates)
    if not verdict.ok:
        # 그럴듯한 환각을 내보내느니 표를 내보낸다.
        notes.append(f"추천이 실재성 검증을 통과하지 못해 폐기했습니다: {verdict.reason}")
        return RecommendResponse(
            question=question,
            recommendation=None,
            candidates=candidates,
            degraded=True,
            notes=notes,
        )

    if verdict.unverifiable_dockerfile_refs:
        # Dockerfile은 사용자가 그대로 복사해 쓰는 산출물이다. 검증되지 않은
        # FROM을 남겨두면 불변식이 여기서 뚫린다. 추천 자체는 유효하므로
        # Dockerfile만 비우고 무엇이 문제였는지 알린다.
        recommendation = recommendation.model_copy(update={"dockerfile": ""})
        notes.append(
            "Dockerfile의 FROM이 추천 이미지를 가리키지 않아 제거했습니다: "
            + ", ".join(verdict.unverifiable_dockerfile_refs)
        )

    if verdict.dropped_alternatives:
        # 검증을 통과하지 못한 대안은 응답에서 지운다. 주 추천은 유효하므로 남긴다.
        recommendation = recommendation.model_copy(
            update={
                "alternatives": [
                    a for a in recommendation.alternatives if a not in verdict.dropped_alternatives
                ]
            }
        )
        notes.append(
            "실재하지 않는 대안 이미지를 제거했습니다: " + ", ".join(verdict.dropped_alternatives)
        )

    return RecommendResponse(
        question=question,
        recommendation=recommendation,
        candidates=candidates,
        degraded=False,
        notes=notes,
    )


def create_app(
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    provider: LLMProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="whatfrom")

    resolved_engine = engine or make_engine(settings.database_url)
    resolved_embedder = embedder or get_embedder(settings.embedder)
    resolved_provider = provider or get_provider(settings.llm_provider)
    factory = sessionmaker(bind=resolved_engine, expire_on_commit=False)

    @contextmanager
    def open_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # 테스트는 app.state.open_session을 롤백 세션을 내주는 팩토리로 갈아끼운다.
    app.state.open_session = open_session

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/recommend", response_model=RecommendResponse)
    def recommend(request: RecommendRequest) -> RecommendResponse:
        return recommend_for_question(
            app.state.open_session, resolved_embedder, resolved_provider, request.question
        )

    return app


app = create_app()
