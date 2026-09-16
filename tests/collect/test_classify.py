# tests/collect/test_classify.py
from datetime import UTC, datetime

from whatfrom.collect.hub import TagRow
from whatfrom.collect.store import ExistingTag, classify_tags

PUSHED = datetime(2026, 9, 1, tzinfo=UTC)


def row(tag: str = "3.14-slim", digest: str | None = "sha256:a", pushed=PUSHED) -> TagRow:
    return TagRow(tag=tag, manifest_digest=digest, last_pushed_at=pushed, variants=())


def test_a_tag_missing_from_the_database_is_new():
    changes = classify_tags([row()], {})

    assert [r.tag for r in changes.new] == ["3.14-slim"]
    assert changes.changed == []
    assert changes.unchanged_ids == []


def test_a_different_digest_is_a_change():
    existing = {"3.14-slim": ExistingTag(7, "sha256:old", PUSHED)}

    changes = classify_tags([row()], existing)

    assert [(i, r.tag) for i, r in changes.changed] == [(7, "3.14-slim")]


def test_a_different_push_time_is_a_change_even_with_the_same_digest():
    existing = {"3.14-slim": ExistingTag(7, "sha256:a", datetime(2026, 8, 1, tzinfo=UTC))}

    changes = classify_tags([row()], existing)

    assert [i for i, _ in changes.changed] == [7]


def test_the_same_digest_and_push_time_is_unchanged():
    existing = {"3.14-slim": ExistingTag(7, "sha256:a", PUSHED)}

    changes = classify_tags([row()], existing)

    assert changes.unchanged_ids == [7]
    assert changes.changed == []


def test_a_null_digest_is_always_a_change():
    """digest가 없으면 이미지가 같은지 알 수 없다. 같다고 단정하지 않는다."""
    existing = {"3.14-slim": ExistingTag(7, None, PUSHED)}

    changes = classify_tags([row(digest=None)], existing)

    assert [i for i, _ in changes.changed] == [7]
