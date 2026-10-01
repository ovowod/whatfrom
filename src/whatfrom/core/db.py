# src/whatfrom/core/db.py
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


def session_factory(engine: Engine) -> Callable[[], AbstractContextManager[Session]]:
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
