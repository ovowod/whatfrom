import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import update

from whatfrom.collect.hub import TagRow, VariantRow, parse_repository
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository
from whatfrom.index.indexer import index_readme
from whatfrom.search.retrieval import search_candidates, search_chunks, search_chunks_by_vector

from .plan_seed import add_repository

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def vector(text: str) -> list[float]:
    return FakeEmbedder().embed([text])[0]


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


def test_search_candidates_orders_tags_by_version_then_shortest_name(session):
    _seed(session)

    candidates = search_candidates(session, FakeEmbedder(), "python", tags_per_repo=5)

    # 픽스처의 두 태그는 마이너가 같으므로 이름 길이로 갈린다.
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


def test_search_candidates_skips_prereleases_and_folds_aliases(session):
    """푸시가 가장 최근인 것이 RC이고, 별칭이 같은 이미지를 가리키는 상황."""
    payload = json.loads((FIXTURES / "hub_repository.json").read_text())
    row = parse_repository(payload)
    upsert_repository(session, row, NOW)
    session.flush()
    upsert_tags(
        session,
        "python",
        [
            # 가장 최근 푸시. 옛 규칙이라면 1위였다.
            TagRow(
                tag="3.15-rc-slim",
                manifest_digest="sha256:rc",
                last_pushed_at=datetime(2026, 9, 5, tzinfo=UTC),
                variants=(VariantRow("linux", "amd64", "", "", "sha256:rc1", 50_000_000),),
            ),
            # 아래 둘은 같은 이미지다. 긴 이름이 접혀야 한다.
            TagRow(
                tag="3.14-alpine3.24",
                manifest_digest="sha256:shared",
                last_pushed_at=datetime(2026, 9, 4, tzinfo=UTC),
                variants=(VariantRow("linux", "amd64", "", "", "sha256:a1", 18_000_000),),
            ),
            TagRow(
                tag="3.14-alpine",
                manifest_digest="sha256:shared",
                last_pushed_at=datetime(2026, 9, 4, tzinfo=UTC),
                variants=(VariantRow("linux", "amd64", "", "", "sha256:a1", 18_000_000),),
            ),
            TagRow(
                tag="3.14-slim",
                manifest_digest="sha256:slim",
                last_pushed_at=datetime(2026, 8, 1, tzinfo=UTC),
                variants=(VariantRow("linux", "amd64", "", "", "sha256:s1", 46_000_000),),
            ),
        ],
        NOW,
    )
    index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()

    candidates = search_candidates(session, FakeEmbedder(), "python", tags_per_repo=5)

    tags = [c.tag for c in candidates]
    assert "3.15-rc-slim" not in tags
    assert "3.14-alpine3.24" not in tags
    assert tags == ["3.14-slim", "3.14-alpine"]
    # 후보끼리 같은 이미지를 가리키지 않는다.
    assert len({c.digest for c in candidates}) == len(candidates)


def test_both_candidate_entry_points_share_the_same_default_limit():
    """한쪽만 바꾸면 호출 경로마다 후보 수가 달라진다."""
    import inspect

    from whatfrom.search.retrieval import search_candidates_by_vector

    by_question = inspect.signature(search_candidates).parameters["tags_per_repo"].default
    by_vector = inspect.signature(search_candidates_by_vector).parameters["tags_per_repo"].default
    assert by_question == by_vector == 20


def test_search_candidates_carry_the_derived_tag_values(session):
    """후보가 image_tags의 파생 컬럼을 싣는다. version은 language_version에서 온다."""
    _seed(session)
    session.execute(
        update(ImageTag)
        .where(ImageTag.tag == "3.13-slim")
        .values(
            language_version="3.13.9",
            version_major_minor="3.13",
            distribution="debian",
            distro_codename="trixie",
            variant="slim",
        )
    )

    candidates = search_candidates(session, FakeEmbedder(), "alpine musl libc", tags_per_repo=5)

    slim = next(c for c in candidates if c.tag == "3.13-slim")
    assert (slim.version, slim.distribution, slim.distro_codename, slim.variant) == (
        "3.13.9",
        "debian",
        "trixie",
        "slim",
    )
    alpine = next(c for c in candidates if c.tag == "3.13-alpine")
    assert (alpine.version, alpine.distribution) == (None, None)


def _seed_long_sections(session) -> None:
    """한 섹션이 청크 여럿이 되도록 800자를 넘는 본문을 넣는다."""
    tags = "alpine slim bookworm trixie jre jdk tag list. " * 150
    add_repository(
        session, "many", f"# Supported tags\n\n{tags}\n\n# Image Variants\n\nalpine musl busybox.\n"
    )
    add_repository(session, "other", "# Image Variants\n\nalpine musl busybox.\n")


def test_search_chunks_returns_one_chunk_per_section(session):
    """청크가 많은 섹션이 상위를 독차지하면 다른 문서가 밀려난다."""
    _seed_long_sections(session)

    hits = search_chunks_by_vector(
        session, vector("alpine slim bookworm trixie jre jdk tag list"), limit=5
    )

    sections = [chunk.document_id for chunk, _ in hits]
    assert len(sections) == len(set(sections))
    assert len({chunk.document.repository for chunk, _ in hits}) > 1


def test_search_chunks_order_does_not_depend_on_the_limit(session):
    """이미지 이름만 다른 청크는 거리가 같다. 동점은 청크 id로 정한다."""
    _seed_long_sections(session)
    v = vector("alpine musl busybox")

    few = search_chunks_by_vector(session, v, limit=2)
    many = search_chunks_by_vector(session, v, limit=50)

    assert [c.id for c, _ in few] == [c.id for c, _ in many[:2]]


def test_search_chunks_in_one_repository_also_keeps_one_chunk_per_section(session):
    _seed_long_sections(session)

    hits = search_chunks_by_vector(
        session, vector("alpine slim bookworm trixie jre jdk tag list"), limit=5, repository="many"
    )

    assert {chunk.document.repository for chunk, _ in hits} == {"many"}
    sections = [chunk.document_id for chunk, _ in hits]
    assert len(sections) == len(set(sections))


def add_chunks(session, repository: str, title: str, texts: list[str]) -> list[int]:
    """거리와 id를 직접 정하려고 청크를 손으로 넣는다. 넣은 순서대로 id가 커진다."""
    session.add(
        Repository(
            name=repository,
            is_official=True,
            source_url=f"https://hub.docker.com/_/{repository}",
            collected_at=NOW,
        )
    )
    session.flush()
    document = Document(
        repository=repository,
        doc_type="readme",
        section_title=title,
        content=" ".join(texts),
        source_url=f"https://hub.docker.com/_/{repository}",
        collected_at=NOW,
    )
    session.add(document)
    session.flush()
    ids = []
    for index, text in enumerate(texts):
        chunk = DocumentChunk(
            document_id=document.id, chunk_index=index, content=text, embedding=vector(text)
        )
        session.add(chunk)
        session.flush()
        ids.append(chunk.id)
    return ids


def test_search_chunks_takes_the_closest_chunk_of_a_section(session):
    """섹션의 대표는 가장 가까운 청크다. 먼 청크를 먼저 넣어 순서로는 못 맞히게 한다."""
    far, near = add_chunks(session, "one", "Image Variants", ["windows server core", "alpine musl"])

    hits = search_chunks_by_vector(session, vector("alpine musl"), limit=5)

    assert [chunk.id for chunk, _ in hits] == [near]
    assert far not in [chunk.id for chunk, _ in hits]


def test_a_tie_inside_a_section_picks_the_smaller_chunk_id(session):
    """같은 본문이면 거리가 같다. 대표는 id가 작은 쪽이다."""
    first, second = add_chunks(session, "two", "Image Variants", ["alpine musl", "alpine musl"])

    hits = search_chunks_by_vector(session, vector("alpine musl"), limit=5)

    assert [chunk.id for chunk, _ in hits] == [first]
    assert second not in [chunk.id for chunk, _ in hits]


def test_a_tie_between_sections_orders_by_chunk_id(session):
    """이미지 이름만 다른 청크는 거리가 같다. 순서는 id로 정한다."""
    [early] = add_chunks(session, "three", "Image Variants", ["alpine musl"])
    [late] = add_chunks(session, "four", "Image Variants", ["alpine musl"])

    hits = search_chunks_by_vector(session, vector("alpine musl"), limit=2)

    assert [chunk.id for chunk, _ in hits] == [early, late]
