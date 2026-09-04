# tests/test_store.py
from sqlalchemy import select

from whatfrom.models import Repository


def test_repositories_table_exists_and_is_empty(session):
    assert session.execute(select(Repository)).scalars().all() == []
