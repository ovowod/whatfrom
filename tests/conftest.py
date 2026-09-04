# tests/conftest.py
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from whatfrom.config import settings
from whatfrom.db import make_engine
from whatfrom.models import Base


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    eng = make_engine(settings.test_database_url)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """매 테스트를 외부 트랜잭션 안에서 돌리고 끝나면 통째로 롤백한다."""
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        transaction.rollback()
        connection.close()
