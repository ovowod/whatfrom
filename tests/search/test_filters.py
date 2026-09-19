# tests/search/test_filters.py
"""검색 조건을 SQL로 옮긴 결과를 실제 테스트 DB에서 확인한다."""

from sqlalchemy import select

from whatfrom.core.models import ImageTag
from whatfrom.search.filters import TagConditions, relaxations, tag_filters

from .plan_seed import add_repository, add_tag

README = "# Image Variants\n\nslim and alpine.\n"


def matching(session, conditions: TagConditions) -> list[str]:
    return sorted(
        session.execute(
            select(ImageTag.tag).where(ImageTag.repository == "py", *tag_filters(conditions))
        ).scalars()
    )


def seed(session) -> None:
    add_repository(session, "py", README)
    add_tag(
        session, "py", "3.14-trixie", version="3.14.7", distribution="debian", codename="trixie"
    )
    add_tag(session, "py", "3.14", version="3.14.7", distribution="debian", codename="trixie")
    add_tag(
        session, "py", "3.14-bookworm", version="3.14.7", distribution="debian", codename="bookworm"
    )
    add_tag(
        session,
        "py",
        "3.14-alpine",
        version="3.14.7",
        distribution="alpine",
        codename="3.24",
        platforms=(("amd64", 20_000_000),),
    )
    # 파생 값이 없는 태그. 태그 이름으로 판정해야 한다.
    add_tag(session, "py", "3.13-alpine-legacy")


def test_no_conditions_keep_every_tag(session):
    seed(session)

    assert len(matching(session, TagConditions())) == 5


def test_required_distribution_matches_a_distribution_or_a_codename(session):
    seed(session)

    assert matching(session, TagConditions(distributions=("bookworm",))) == ["3.14-bookworm"]
    assert matching(session, TagConditions(distributions=("debian",))) == [
        "3.14",
        "3.14-bookworm",
        "3.14-trixie",
    ]


def test_an_excluded_codename_removes_an_alias_whose_name_hides_it(session):
    """python:3.14는 이름에 배포판이 없지만 trixie로 파생됐다."""
    seed(session)

    assert matching(session, TagConditions(exclude_distributions=("trixie",))) == [
        "3.13-alpine-legacy",
        "3.14-alpine",
        "3.14-bookworm",
    ]


def test_an_exclusion_keeps_tags_whose_derived_values_are_other(session):
    """NULL 비교가 NOT을 NULL로 만들어 행을 지우면 안 된다. 코드네임이 없는 행도 남아야 한다."""
    seed(session)

    assert "3.14-bookworm" in matching(session, TagConditions(exclude_distributions=("alpine",)))


def test_a_tag_without_derived_values_is_judged_by_its_name(session):
    seed(session)

    assert "3.13-alpine-legacy" not in matching(
        session, TagConditions(exclude_distributions=("alpine",))
    )
    assert "3.13-alpine-legacy" in matching(session, TagConditions(distributions=("alpine",)))


def test_version_prefix_uses_the_derived_version_and_its_boundaries(session):
    seed(session)

    assert len(matching(session, TagConditions(version_prefix="3.14"))) == 4
    assert matching(session, TagConditions(version_prefix="3.1")) == []


def test_version_prefix_falls_back_to_the_tag_name(session):
    seed(session)

    assert matching(session, TagConditions(version_prefix="3.13")) == ["3.13-alpine-legacy"]


def test_an_underscore_in_the_prefix_is_not_a_wildcard(session):
    """21.0.1_ 로 시작하는 패턴에서 _가 와일드카드면 21.0.12_8이 걸린다."""
    add_repository(session, "py", README)
    add_tag(session, "py", "21-jdk", version="21.0.12_8")

    assert matching(session, TagConditions(version_prefix="21.0.1")) == []
    assert matching(session, TagConditions(version_prefix="21.0.12")) == ["21-jdk"]


def test_architecture_requires_a_linux_platform(session):
    seed(session)

    assert "3.14-alpine" not in matching(session, TagConditions(architectures=("arm64",)))
    assert "3.14-alpine" in matching(session, TagConditions(architectures=("amd64",)))


def test_size_is_checked_on_the_first_required_architecture(session):
    seed(session)

    assert matching(session, TagConditions(max_size_mb=30)) == ["3.14-alpine"]
    assert matching(session, TagConditions(architectures=("arm64",), max_size_mb=55)) == []


def test_relaxations_drop_the_weakest_condition_first():
    conditions = TagConditions(
        version_prefix="3.12",
        architectures=("arm64",),
        distributions=("bookworm",),
        exclude_distributions=("trixie",),
        max_size_mb=100,
    )

    stages = list(relaxations(conditions))

    assert [note for _, note in stages] == [
        None,
        "크기 조건(100MB 이하)",
        "배포판 조건(bookworm, trixie 제외)",
        "아키텍처 조건(arm64)",
        "버전 조건(3.12)",
    ]
    assert stages[-1][0] == TagConditions()


def test_relaxations_skip_conditions_that_are_absent():
    stages = list(relaxations(TagConditions(version_prefix="17")))

    assert [note for _, note in stages] == [None, "버전 조건(17)"]
