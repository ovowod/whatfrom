# src/whatfrom/collect/store.py
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from whatfrom.collect.hub import RepositoryRow, TagRow
from whatfrom.models import ImageTag, ImageVariant, Repository


def upsert_repository(session: Session, row: RepositoryRow, collected_at: datetime) -> Repository:
    repo = session.get(Repository, row.name)
    if repo is None:
        repo = Repository(name=row.name)
        session.add(repo)
    repo.is_official = row.is_official
    repo.description = row.description
    repo.source_url = row.source_url
    repo.collected_at = collected_at
    return repo


def upsert_tags(
    session: Session, repository: str, rows: list[TagRow], collected_at: datetime
) -> int:
    for row in rows:
        tag = session.execute(
            select(ImageTag).where(ImageTag.repository == repository, ImageTag.tag == row.tag)
        ).scalar_one_or_none()
        if tag is None:
            tag = ImageTag(repository=repository, tag=row.tag)
            session.add(tag)

        tag.manifest_digest = row.manifest_digest
        tag.last_pushed_at = row.last_pushed_at
        tag.collected_at = collected_at

        # 변종은 통째로 교체한다. 아키텍처가 사라지는 경우가 실제로 있다.
        tag.variants.clear()
        session.flush()
        for variant in row.variants:
            tag.variants.append(
                ImageVariant(
                    os=variant.os,
                    architecture=variant.architecture,
                    arch_variant=variant.arch_variant,
                    os_version=variant.os_version,
                    digest=variant.digest,
                    size_bytes=variant.size_bytes,
                )
            )
    return len(rows)
