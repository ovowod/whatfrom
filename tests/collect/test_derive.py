# tests/collect/test_derive.py
"""derive_repository: 수집된 태그에 파생 값을 채우는 DB 경로."""

from datetime import UTC, datetime

from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from whatfrom.collect.derive import derive_repository
from whatfrom.collect.hub import RepositoryRow, TagRow, VariantRow
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.models import ImageTag

NOW = datetime(2026, 9, 19, tzinfo=UTC)
LINUX = (
    VariantRow("linux", "amd64", "", "", "sha256:l-amd64", 1),
    VariantRow("linux", "arm64", "v8", "", "sha256:l-arm64", 2),
)
WINDOWS = (VariantRow("windows", "amd64", "", "10.0.26100.1", "sha256:w-amd64", 3),)


def seed(session, tags: list[tuple[str, tuple[VariantRow, ...]]]) -> None:
    upsert_repository(
        session,
        RepositoryRow("python", True, "", "https://hub.docker.com/_/python", ""),
        NOW,
    )
    upsert_tags(
        session,
        "python",
        [TagRow(name, f"sha256:index-{name}", NOW, variants) for name, variants in tags],
        NOW,
    )
    session.flush()


def row(session, tag: str) -> tuple:
    return tuple(
        session.execute(
            select(
                ImageTag.language_version,
                ImageTag.version_major_minor,
                ImageTag.distribution,
                ImageTag.distro_codename,
                ImageTag.variant,
            ).where(ImageTag.tag == tag)
        ).one()
    )


def test_an_alias_bundling_windows_images_inherits_from_the_linux_twin(session):
    """python:3.14는 Windows 이미지도 담아 index digest가 3.14-trixie와 다르다. Linux 쪽은 같다."""
    seed(session, [("3.14", LINUX + WINDOWS), ("3.14-trixie", LINUX), ("3.14.7", LINUX)])

    outcome = derive_repository(session, "python")

    assert row(session, "3.14") == ("3.14.7", "3.14", "debian", "trixie", "full")
    assert (outcome.total, outcome.changed, outcome.with_distribution) == (3, 3, 3)
    assert (outcome.conflict_fields, outcome.conflict_tags) == (0, 0)


def test_a_windows_only_tag_is_not_grouped_with_anything(session):
    seed(session, [("3.14-windowsservercore", WINDOWS), ("3.14-trixie", LINUX)])

    derive_repository(session, "python")

    assert row(session, "3.14-windowsservercore") == (
        "3.14",
        "3.14",
        "windows",
        None,
        "windowsservercore",
    )


def test_running_again_changes_nothing(session):
    seed(session, [("3.14", LINUX), ("3.14-trixie", LINUX)])
    derive_repository(session, "python")

    assert derive_repository(session, "python").changed == 0


def _count_statements(engine, n: int) -> int:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    executed: list[str] = []
    try:
        seed(session, [(f"3.{i}-trixie", LINUX) for i in range(n)])

        def count(conn, cursor, statement, parameters, context, executemany):
            executed.append(statement)

        event.listen(connection, "before_cursor_execute", count)
        try:
            derive_repository(session, "python")
        finally:
            event.remove(connection, "before_cursor_execute", count)
    finally:
        session.close()
        transaction.rollback()
        connection.close()
    return len(executed)


def test_statement_count_does_not_grow_with_the_repository(engine):
    """태그마다 UPDATE가 나가면 리포지토리 하나(1,000개)가 1,000번 왕복한다."""
    assert _count_statements(engine, 2) == _count_statements(engine, 50)
