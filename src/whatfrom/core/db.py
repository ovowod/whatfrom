# src/whatfrom/core/db.py
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from whatfrom.core.config import settings


def make_engine(url: str, statement_timeout_ms: int | None = None) -> Engine:
    """pool 크기는 settings에서 읽는다. statement timeout은 준 경우에만 건다."""
    connect_args = {}
    if statement_timeout_ms is not None:
        connect_args["options"] = f"-c statement_timeout={statement_timeout_ms}"
    return create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        connect_args=connect_args,
    )


# DB 구간마다 session을 여는 factory. 추천 경로와 eval runner가 받는다.
SessionFactory = Callable[[], AbstractContextManager[Session]]


def session_factory(engine: Engine) -> SessionFactory:
    """DB 구간마다 session을 열고 닫는 factory. commit하지 않고 close만 한다.

    추천 경로(recommend_for_question)가 받는 형태다. embedding·LLM 응답을 기다리는 동안
    DB 연결과 transaction을 붙잡지 않도록, 호출하는 쪽이 DB를 쓰는 구간마다 연다.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def open_session() -> Generator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return open_session


@contextmanager
def session_scope(engine: Engine) -> Generator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
