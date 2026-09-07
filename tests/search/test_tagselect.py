from datetime import UTC, datetime

from whatfrom.search.tagselect import TagRef, select_tags


def ref(
    tag_id: int,
    tag: str,
    digest: str | None = "",
    pushed: datetime | None = None,
) -> TagRef:
    """테스트용 TagRef. digest를 안 주면 tag_id로 유일한 값을 만든다."""
    return TagRef(
        id=tag_id,
        tag=tag,
        manifest_digest=f"sha256:{tag_id}" if digest == "" else digest,
        last_pushed_at=pushed,
    )


def test_prerelease_tags_never_become_candidates():
    """rc만 거르면 알파와 베타를 놓친다. 파이썬은 PEP 440 표기를 쓴다."""
    tags = [
        ref(1, "3.15.0a8"),
        ref(2, "3.15.0b1"),
        ref(3, "3.15.0rc2"),
        ref(4, "3.15-rc"),
        ref(5, "3.15-rc-slim"),
        ref(6, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim"]


def test_milestone_tags_are_excluded():
    """redis의 8.8-m03 같은 마일스톤 빌드는 후보가 되지 않는다."""
    tags = [ref(1, "8.8-m03"), ref(2, "8.8-slim")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["8.8-slim"]


def test_devel_tag_is_excluded():
    """ubuntu의 devel은 개발 브랜치라 후보가 되지 않는다."""
    tags = [ref(1, "devel"), ref(2, "26.4-slim")]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["26.4-slim"]


def test_a_tag_sharing_a_digest_with_a_prerelease_is_excluded_too():
    """ubuntu의 devel과 26.10은 같은 digest를 가리킨다.

    26.10은 이름만 보면 안정 버전 같지만 실제로는 아직 나오지 않은
    개발 브랜치라, digest로 전파해서 함께 뺀다.
    """
    tags = [
        ref(1, "devel", digest="sha256:same"),
        ref(2, "26.10", digest="sha256:same"),
        ref(3, "26.4-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["26.4-slim"]


def test_digest_propagation_ignores_unknown_digests():
    """digest가 None인 태그는 전파의 근거로도, 대상으로도 쓰지 않는다."""
    tags = [
        ref(1, "devel", digest=None),
        ref(2, "26.4-slim", digest=None),
    ]
    assert {t.tag for t in select_tags(tags, limit=10)} == {"26.4-slim"}


def test_floating_and_patch_pinned_tags_are_not_candidates():
    """latest와 3은 다음 달에 다른 이미지가 되고, 3.14.7-slim은 패치에 묶인다."""
    tags = [
        ref(1, "latest"),
        ref(2, "3"),
        ref(3, "3.14.7-slim"),
        ref(4, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-slim"]


def test_same_digest_keeps_only_the_shorter_name():
    """3.14-alpine3.24는 3.14-alpine과 같은 이미지다."""
    tags = [
        ref(1, "3.14-alpine3.24", digest="sha256:same"),
        ref(2, "3.14-alpine", digest="sha256:same"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-alpine"]


def test_same_digest_and_same_length_breaks_the_tie_alphabetically():
    tags = [
        ref(1, "3.14-zzzz", digest="sha256:same"),
        ref(2, "3.14-aaaa", digest="sha256:same"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.14-aaaa"]


def test_tags_without_a_digest_are_not_folded():
    """동일성을 알 수 없는 것을 같다고 단정하지 않는다."""
    tags = [
        ref(1, "3.14-slim", digest=None),
        ref(2, "3.14-alpine", digest=None),
    ]
    assert {t.tag for t in select_tags(tags, limit=10)} == {"3.14-slim", "3.14-alpine"}


def test_newer_versions_come_first():
    tags = [ref(1, "3.9-slim"), ref(2, "3.14-slim"), ref(3, "4.0-slim"), ref(4, "3.10-slim")]
    assert [t.tag for t in select_tags(tags, limit=10)] == [
        "4.0-slim",
        "3.14-slim",
        "3.10-slim",
        "3.9-slim",
    ]


def test_shorter_names_come_first_within_the_same_version():
    """짧은 이름이 각 변종의 대표다. 사전순만 쓰면 alpine 파생형이 자리를 먹는다."""
    tags = [
        ref(1, "3.14-alpine3.22"),
        ref(2, "3.14-bookworm"),
        ref(3, "3.14"),
        ref(4, "3.14-slim"),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == [
        "3.14",
        "3.14-slim",
        "3.14-bookworm",
        "3.14-alpine3.22",
    ]


def test_falls_back_to_recent_push_when_no_tag_is_a_minor_alias():
    """debian:bookworm처럼 숫자 버전이 없는 리포. 후보를 0개로 만들지 않는다."""
    tags = [
        ref(1, "bookworm", pushed=datetime(2026, 9, 1, tzinfo=UTC)),
        ref(2, "trixie", pushed=datetime(2026, 9, 3, tzinfo=UTC)),
        ref(3, "bullseye", pushed=datetime(2026, 8, 1, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["trixie", "bookworm", "bullseye"]


def test_fallback_still_excludes_prereleases():
    """이동 별칭이 없어도 프리릴리스는 후보가 되지 않는다."""
    tags = [
        ref(1, "trixie-rc", pushed=datetime(2026, 9, 5, tzinfo=UTC)),
        ref(2, "bookworm", pushed=datetime(2026, 9, 1, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["bookworm"]


def test_fallback_puts_tags_without_a_push_time_last():
    tags = [
        ref(1, "bookworm", pushed=None),
        ref(2, "trixie", pushed=datetime(2026, 9, 3, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["trixie", "bookworm"]


def test_returns_prereleases_when_a_repo_has_nothing_else():
    """후보가 0개인 것보다는 프리릴리스라도 돌려주는 편이 낫다."""
    tags = [
        ref(1, "3.15.0a8", pushed=datetime(2026, 9, 1, tzinfo=UTC)),
        ref(2, "3.15.0b1", pushed=datetime(2026, 9, 3, tzinfo=UTC)),
    ]
    assert [t.tag for t in select_tags(tags, limit=10)] == ["3.15.0b1", "3.15.0a8"]


def test_returns_what_exists_when_fewer_than_the_limit():
    assert len(select_tags([ref(1, "3.14-slim")], limit=5)) == 1


def test_returns_empty_for_no_tags():
    assert select_tags([], limit=5) == []
