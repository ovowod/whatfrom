# tests/test_store.py
from datetime import UTC, datetime

from sqlalchemy import select

from whatfrom.collect.hub import RepositoryRow, TagRow, VariantRow
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.models import ImageTag, ImageVariant, Repository

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _repo_row() -> RepositoryRow:
    return RepositoryRow(
        name="python",
        is_official=True,
        description="Python language",
        source_url="https://hub.docker.com/_/python",
        readme="# Image Variants\n\nslim and alpine.\n",
    )


def _tag_row(tag: str = "3.13-slim") -> TagRow:
    return TagRow(
        tag=tag,
        manifest_digest="sha256:aaa",
        last_pushed_at=datetime(2026, 9, 1, tzinfo=UTC),
        variants=(
            VariantRow("linux", "amd64", "", "", "sha256:bbb", 46992930),
            VariantRow("linux", "arm64", "v8", "", "sha256:ccc", 47609438),
        ),
    )


def test_repositories_table_exists_and_is_empty(session):
    assert session.execute(select(Repository)).scalars().all() == []


def test_upsert_repository_inserts_then_updates_in_place(session):
    upsert_repository(session, _repo_row(), NOW)
    session.flush()

    changed = RepositoryRow(
        name="python",
        is_official=True,
        description="new description",
        source_url="https://hub.docker.com/_/python",
        readme="# Image Variants\n",
    )
    upsert_repository(session, changed, NOW)
    session.flush()

    repos = session.execute(select(Repository)).scalars().all()
    assert len(repos) == 1
    assert repos[0].description == "new description"
    assert repos[0].is_official is True


def test_upsert_tags_writes_tag_and_its_architecture_variants(session):
    upsert_repository(session, _repo_row(), NOW)
    written = upsert_tags(session, "python", [_tag_row()], NOW)
    session.flush()

    assert written == 1
    tag = session.execute(select(ImageTag)).scalars().one()
    assert tag.tag == "3.13-slim"
    assert tag.repository == "python"
    # 파생 컬럼은 F6까지 비어 있다.
    assert tag.distribution is None
    assert tag.variant is None

    arches = sorted(v.architecture for v in tag.variants)
    assert arches == ["amd64", "arm64"]


def test_upsert_tags_persists_windows_manifests_differing_only_by_os_version(session):
    """실물 회귀: Docker Hub는 같은 (os, arch)에 커널 버전별 매니페스트를 낸다.

    os_version이 식별자에 없으면 이 둘이 UNIQUE 충돌을 일으켜 collect 전체가 죽는다.
    """
    upsert_repository(session, _repo_row(), NOW)
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-windowsservercore",
                manifest_digest="sha256:win",
                last_pushed_at=NOW,
                variants=(
                    VariantRow("windows", "amd64", "", "10.0.20348.5499", "sha256:w1", 2256084111),
                    VariantRow("windows", "amd64", "", "10.0.26100.33296", "sha256:w2", 2513037866),
                ),
            )
        ],
        NOW,
    )
    session.flush()

    variants = session.execute(select(ImageVariant)).scalars().all()
    assert len(variants) == 2
    assert {v.os_version for v in variants} == {"10.0.20348.5499", "10.0.26100.33296"}
    assert {v.size_bytes for v in variants} == {2256084111, 2513037866}


def test_upsert_tags_is_idempotent_and_replaces_variants(session):
    upsert_repository(session, _repo_row(), NOW)
    upsert_tags(session, "python", [_tag_row()], NOW)
    session.flush()

    shrunk = TagRow(
        tag="3.13-slim",
        manifest_digest="sha256:zzz",
        last_pushed_at=datetime(2026, 9, 2, tzinfo=UTC),
        variants=(VariantRow("linux", "amd64", "", "", "sha256:bbb", 1),),
    )
    upsert_tags(session, "python", [shrunk], NOW)
    session.flush()

    tags = session.execute(select(ImageTag)).scalars().all()
    assert len(tags) == 1
    assert tags[0].manifest_digest == "sha256:zzz"

    variants = session.execute(select(ImageVariant)).scalars().all()
    assert len(variants) == 1
    assert variants[0].size_bytes == 1
