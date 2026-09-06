import json
from datetime import UTC, datetime
from pathlib import Path

from whatfrom.collect.hub import TagRow, VariantRow, parse_repository
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.embed import FakeEmbedder
from whatfrom.index.indexer import index_readme
from whatfrom.search.retrieval import search_candidates, search_chunks

FIXTURES = Path(__file__).parents[1] / "fixtures"
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


def test_search_candidates_reports_every_platform_without_collapsing(session):
    _seed(session)

    candidates = search_candidates(session, FakeEmbedder(), "python slim", tags_per_repo=5)
    slim = next(c for c in candidates if c.tag == "3.13-slim")

    assert [(p.os, p.architecture) for p in slim.platforms] == [
        ("linux", "amd64"),
        ("linux", "arm64"),
    ]
    assert [p.size_bytes for p in slim.platforms] == [46992930, 47609438]
    assert slim.digest == "sha256:aaa"


def test_search_candidates_keeps_same_architecture_on_different_os(session):
    """amd64는 linux와 windows 양쪽에 있다. 하나로 접으면 한쪽이 사라진다."""
    _seed(session)
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-multi",
                manifest_digest="sha256:multi",
                last_pushed_at=datetime(2026, 9, 3, tzinfo=UTC),
                variants=(
                    VariantRow("linux", "amd64", "", "", "sha256:l", 400_000_000),
                    VariantRow(
                        "windows", "amd64", "", "10.0.20348.5499", "sha256:w", 2_400_000_000
                    ),
                ),
            )
        ],
        NOW,
    )
    session.flush()

    candidates = search_candidates(session, FakeEmbedder(), "python", tags_per_repo=10)
    multi = next(c for c in candidates if c.tag == "3.13-multi")

    assert len(multi.platforms) == 2
    assert {p.size_bytes for p in multi.platforms} == {400_000_000, 2_400_000_000}


def test_search_candidates_orders_tags_by_most_recent_push(session):
    _seed(session)

    candidates = search_candidates(session, FakeEmbedder(), "python", tags_per_repo=5)

    assert [c.tag for c in candidates] == ["3.13-slim", "3.13-alpine"]


def test_search_candidates_orders_windows_kernel_versions_deterministically(session):
    """os_version만 다른 두 행은 정렬 키가 os_version을 포함해야 순서가 정해진다.

    실물 DB에 이런 동률 조합이 36개 있다. 키에서 빠지면 순서가 Postgres의
    행 반환 순서에 맡겨져 실행마다 달라질 수 있다.
    """
    _seed(session)
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-win",
                manifest_digest="sha256:win",
                last_pushed_at=datetime(2026, 9, 3, tzinfo=UTC),
                variants=(
                    # 일부러 역순으로 넣는다. 정렬이 실제로 일어나야 통과한다.
                    VariantRow(
                        "windows", "amd64", "", "10.0.26100.33296", "sha256:new", 2_513_037_866
                    ),
                    VariantRow(
                        "windows", "amd64", "", "10.0.20348.5499", "sha256:old", 2_256_084_111
                    ),
                ),
            )
        ],
        NOW,
    )
    session.flush()

    win = next(
        c
        for c in search_candidates(session, FakeEmbedder(), "python", tags_per_repo=10)
        if c.tag == "3.13-win"
    )

    assert [p.os_version for p in win.platforms] == [
        "10.0.20348.5499",
        "10.0.26100.33296",
    ]
    assert [p.digest for p in win.platforms] == ["sha256:old", "sha256:new"]


def test_search_candidates_returns_empty_when_nothing_is_indexed(session):
    assert search_candidates(session, FakeEmbedder(), "anything") == []
