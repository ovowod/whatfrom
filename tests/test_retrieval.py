import json
from datetime import UTC, datetime
from pathlib import Path

from whatfrom.collect.hub import TagRow, VariantRow, parse_repository
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.embed import FakeEmbedder
from whatfrom.indexer import index_readme
from whatfrom.retrieval import search_candidates, search_chunks

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _seed(session):
    payload = json.loads((FIXTURES / "hub_repository.json").read_text())
    row = parse_repository(payload)
    upsert_repository(session, row, NOW)
    session.flush()
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-slim",
                manifest_digest="sha256:aaa",
                last_pushed_at=datetime(2026, 9, 2, tzinfo=UTC),
                variants=(
                    VariantRow("linux", "amd64", "", "", "sha256:bbb", 46992930),
                    VariantRow("linux", "arm64", "v8", "", "sha256:ccc", 47609438),
                ),
            ),
            TagRow(
                tag="3.13-alpine",
                manifest_digest="sha256:ddd",
                last_pushed_at=datetime(2026, 9, 1, tzinfo=UTC),
                variants=(VariantRow("linux", "amd64", "", "", "sha256:eee", 18500000),),
            ),
        ],
        NOW,
    )
    index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()


def test_search_chunks_ranks_the_alpine_section_first_for_a_musl_question(session):
    _seed(session)

    hits = search_chunks(session, FakeEmbedder(), "alpine musl libc image size", limit=3)

    assert hits
    top_chunk, distance = hits[0]
    assert "alpine" in top_chunk.document.section_title.lower()
    assert 0.0 <= distance <= 2.0


def test_search_chunks_respects_the_limit(session):
    _seed(session)

    assert len(search_chunks(session, FakeEmbedder(), "python image", limit=2)) == 2


def test_search_candidates_returns_real_tags_with_evidence(session):
    _seed(session)

    candidates = search_candidates(session, FakeEmbedder(), "alpine musl libc", tags_per_repo=5)

    assert candidates
    assert {c.image for c in candidates} <= {"python:3.13-slim", "python:3.13-alpine"}
    assert all(c.repository == "python" for c in candidates)
    assert all(c.evidence for c in candidates)
    assert all(c.source_url.startswith("https://hub.docker.com/_/") for c in candidates)


def test_search_candidates_reports_architectures_and_size(session):
    _seed(session)

    candidates = search_candidates(session, FakeEmbedder(), "python slim", tags_per_repo=5)
    slim = next(c for c in candidates if c.tag == "3.13-slim")

    assert sorted(slim.architectures) == ["amd64", "arm64"]
    assert slim.size_bytes == 46992930
    assert slim.digest == "sha256:aaa"


def test_search_candidates_orders_tags_by_most_recent_push(session):
    _seed(session)

    candidates = search_candidates(session, FakeEmbedder(), "python", tags_per_repo=5)

    assert [c.tag for c in candidates] == ["3.13-slim", "3.13-alpine"]


def test_search_candidates_returns_empty_when_nothing_is_indexed(session):
    assert search_candidates(session, FakeEmbedder(), "anything") == []
