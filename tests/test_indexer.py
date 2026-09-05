import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from whatfrom.collect.hub import parse_repository
from whatfrom.collect.store import upsert_repository
from whatfrom.embed import EMBEDDING_DIM, FakeEmbedder
from whatfrom.indexer import index_readme
from whatfrom.models import Document, DocumentChunk

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _seed_repository(session):
    payload = json.loads((FIXTURES / "hub_repository.json").read_text())
    row = parse_repository(payload)
    upsert_repository(session, row, NOW)
    session.flush()
    return row


def test_index_readme_creates_documents_per_section(session):
    row = _seed_repository(session)

    index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()

    titles = session.execute(select(Document.section_title)).scalars().all()
    assert any("alpine" in t for t in titles)
    assert all(d.doc_type == "readme" for d in session.execute(select(Document)).scalars())


def test_index_readme_fills_every_chunk_embedding(session):
    row = _seed_repository(session)

    created = index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()

    chunks = session.execute(select(DocumentChunk)).scalars().all()
    assert created == len(chunks)
    assert chunks
    assert all(c.embedding is not None for c in chunks)
    assert all(len(c.embedding) == EMBEDDING_DIM for c in chunks)


def test_index_readme_rejects_an_embedder_with_the_wrong_dimension(session):
    """차원이 어긋난 임베더는 한 건도 계산하기 전에 거부한다."""

    class WrongDimEmbedder:
        dimension = 768

        def embed(self, texts):
            raise AssertionError("should not have been called")

    row = _seed_repository(session)

    with pytest.raises(ValueError, match="768-dim"):
        index_readme(session, "python", row.readme, row.source_url, WrongDimEmbedder(), NOW)


def test_index_readme_is_idempotent(session):
    row = _seed_repository(session)

    first = index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()
    second = index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()

    assert first == second
    assert len(session.execute(select(DocumentChunk)).scalars().all()) == first
