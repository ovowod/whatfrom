from sqlalchemy import text

from whatfrom import api
from whatfrom.core.config import settings
from whatfrom.core.db import make_engine


def _statement_timeout(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SHOW statement_timeout")).scalar_one()


def test_an_engine_without_a_timeout_leaves_statements_unbounded() -> None:
    """CLI batch는 긴 upsert와 색인을 하므로 statement timeout을 걸지 않는다."""
    engine = make_engine(settings.test_database_url)
    try:
        assert _statement_timeout(engine) == "0"
    finally:
        engine.dispose()


def test_an_engine_applies_the_given_statement_timeout() -> None:
    engine = make_engine(settings.test_database_url, statement_timeout_ms=1234)
    try:
        assert _statement_timeout(engine) == "1234ms"
    finally:
        engine.dispose()


def test_the_pool_size_comes_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "db_pool_size", 7)
    monkeypatch.setattr(settings, "db_max_overflow", 3)
    monkeypatch.setattr(settings, "db_pool_timeout_seconds", 12.0)
    engine = make_engine(settings.test_database_url)
    try:
        assert (engine.pool.size(), engine.pool._max_overflow, engine.pool._timeout) == (7, 3, 12.0)
    finally:
        engine.dispose()


def test_the_api_engine_uses_the_statement_timeout_setting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", settings.test_database_url)
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 4321)

    app = api.create_app()

    with app.state.open_session() as session:
        assert session.execute(text("SHOW statement_timeout")).scalar_one() == "4321ms"
