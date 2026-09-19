# tests/search/plan_seed.py
"""검색 조건 테스트가 쓰는 작은 DB. 파생 컬럼과 플랫폼 행을 직접 넣는다."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.models import ImageTag, ImageVariant, Repository
from whatfrom.index.indexer import index_readme

NOW = datetime(2026, 9, 19, tzinfo=UTC)
DAY = timedelta(days=1)
LINUX_BOTH = (("amd64", 50_000_000), ("arm64", 60_000_000))


def add_repository(session: Session, name: str, readme: str) -> None:
    session.add(
        Repository(
            name=name,
            is_official=True,
            source_url=f"https://hub.docker.com/_/{name}",
            collected_at=NOW,
        )
    )
    session.flush()
    index_readme(session, name, readme, f"https://hub.docker.com/_/{name}", FakeEmbedder(), NOW)
    session.flush()


def add_tag(
    session: Session,
    repository: str,
    tag: str,
    *,
    version: str | None = None,
    distribution: str | None = None,
    codename: str | None = None,
    platforms: tuple[tuple[str, int], ...] = LINUX_BOTH,
    pushed: datetime = NOW,
) -> ImageTag:
    row = ImageTag(
        repository=repository,
        tag=tag,
        manifest_digest=f"sha256:{repository}-{tag}",
        last_pushed_at=pushed,
        collected_at=NOW,
        language_version=version,
        distribution=distribution,
        distro_codename=codename,
        variant="full",
    )
    session.add(row)
    session.flush()
    for architecture, size in platforms:
        session.add(
            ImageVariant(
                tag_id=row.id,
                os="linux",
                architecture=architecture,
                arch_variant="",
                os_version="",
                digest=f"sha256:{repository}-{tag}-{architecture}",
                size_bytes=size,
            )
        )
    session.flush()
    return row
