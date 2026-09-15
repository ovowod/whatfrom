# src/whatfrom/collect/store.py
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from whatfrom.collect.hub import RepositoryRow, TagRow
from whatfrom.core.models import ImageTag, ImageVariant, Repository


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


@dataclass(frozen=True)
class ExistingTag:
    id: int
    manifest_digest: str | None
    last_pushed_at: datetime | None


@dataclass(frozen=True)
class TagChanges:
    new: list[TagRow]
    changed: list[tuple[int, TagRow]]
    unchanged_ids: list[int]


def classify_tags(rows: list[TagRow], existing: dict[str, ExistingTag]) -> TagChanges:
    """받은 태그를 신규·변경·미변경으로 나눈다. DB를 모른다.

    manifest_digest는 모든 플랫폼 manifest를 묶은 index의 digest라 어느 플랫폼이
    바뀌어도 달라진다. digest가 없으면 이미지가 같은지 알 수 없으니 변경으로 본다.
    """
    new: list[TagRow] = []
    changed: list[tuple[int, TagRow]] = []
    unchanged: list[int] = []
    for row in rows:
        current = existing.get(row.tag)
        if current is None:
            new.append(row)
        elif (
            row.manifest_digest is None
            or current.manifest_digest != row.manifest_digest
            or current.last_pushed_at != row.last_pushed_at
        ):
            changed.append((current.id, row))
        else:
            unchanged.append(current.id)
    return TagChanges(new=new, changed=changed, unchanged_ids=unchanged)


def upsert_tags(
    session: Session, repository: str, rows: list[TagRow], collected_at: datetime
) -> int:
    """한 페이지의 태그를 저장하고, 신규·변경 태그 수를 돌려준다.

    구성이 같으면 태그 수와 무관하게 SQL 실행 횟수가 같다. ORM flush에 맡기면
    바뀐 컬럼 조합마다 UPDATE가 나뉘고, tag.variants 지연 조회는 태그마다
    SELECT를 한 번씩 보낸다. 그래서 일괄 문을 직접 쓴다.

    기존 태그는 엔티티가 아니라 컬럼으로만 읽는다. 엔티티로 읽으면 뒤따르는
    일괄 UPDATE가 세션의 객체를 갱신하지 않아, 같은 세션에서 다시 읽을 때 옛 값이 보인다.
    """
    if not rows:
        return 0

    existing = {
        found.tag: ExistingTag(found.id, found.manifest_digest, found.last_pushed_at)
        for found in session.execute(
            select(
                ImageTag.id, ImageTag.tag, ImageTag.manifest_digest, ImageTag.last_pushed_at
            ).where(ImageTag.repository == repository, ImageTag.tag.in_([r.tag for r in rows]))
        )
    }
    changes = classify_tags(rows, existing)
    rewrite: list[tuple[int, TagRow]] = []

    if changes.new:
        inserted = session.execute(
            insert(ImageTag).returning(ImageTag.id, ImageTag.tag),
            [
                {
                    "repository": repository,
                    "tag": row.tag,
                    "manifest_digest": row.manifest_digest,
                    "last_pushed_at": row.last_pushed_at,
                    "collected_at": collected_at,
                }
                for row in changes.new
            ],
        ).all()
        ids_by_tag = {tag: tag_id for tag_id, tag in inserted}
        rewrite.extend((ids_by_tag[row.tag], row) for row in changes.new)

    if changes.changed:
        session.execute(
            update(ImageTag),
            [
                {
                    "id": tag_id,
                    "manifest_digest": row.manifest_digest,
                    "last_pushed_at": row.last_pushed_at,
                    "collected_at": collected_at,
                }
                for tag_id, row in changes.changed
            ],
        )
        # 변종은 통째로 교체한다. 아키텍처가 사라지는 경우가 실제로 있다.
        session.execute(
            delete(ImageVariant)
            .where(ImageVariant.tag_id.in_([tag_id for tag_id, _ in changes.changed]))
            .execution_options(synchronize_session=False)
        )
        rewrite.extend(changes.changed)

    variants = [
        {
            "tag_id": tag_id,
            "os": variant.os,
            "architecture": variant.architecture,
            "arch_variant": variant.arch_variant,
            "os_version": variant.os_version,
            "digest": variant.digest,
            "size_bytes": variant.size_bytes,
        }
        for tag_id, row in rewrite
        for variant in row.variants
    ]
    if variants:
        session.execute(insert(ImageVariant), variants)

    if changes.unchanged_ids:
        # 이미지는 그대로여도 응답의 "수집 시점"은 마지막으로 확인한 시각이어야 한다.
        session.execute(
            update(ImageTag)
            .where(ImageTag.id.in_(changes.unchanged_ids))
            .values(collected_at=collected_at)
            .execution_options(synchronize_session=False)
        )

    return len(rewrite)
