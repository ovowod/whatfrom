# tests/collect/test_resolve_aliases.py
"""같은 이미지의 다른 태그에서 파생 값을 물려받는 규칙. DB 없이 돈다."""

from whatfrom.collect.derive import Derived, resolve_aliases
from whatfrom.collect.tagparse import parse_tag

TRIXIE = frozenset({("amd64", None, "sha256:t-amd64"), ("arm64", "v8", "sha256:t-arm64")})
ALPINE = frozenset({("amd64", None, "sha256:a-amd64")})


def resolve(repository: str, groups: dict[str, frozenset], lonely: tuple[str, ...] = ()):
    """groups는 태그 → 지문. lonely는 지문이 없는 태그다."""
    tags = [*groups, *lonely]
    return resolve_aliases(
        {tag: parse_tag(tag, repository) for tag in tags},
        {tag: groups.get(tag, frozenset()) for tag in tags},
    )


def test_an_alias_inherits_the_distribution_and_codename_of_the_same_image():
    result = resolve("python", {"3.14": TRIXIE, "3.14-trixie": TRIXIE, "3.14.7": TRIXIE})

    assert result.values["3.14"] == Derived("3.14.7", "3.14", "debian", "trixie", "full")


def test_a_tag_without_a_version_inherits_the_end_of_the_version_chain():
    result = resolve("node", {"lts": TRIXIE, "24": TRIXIE, "24.21": TRIXIE, "24.21.0": TRIXIE})

    assert result.values["lts"].language_version == "24.21.0"
    assert result.values["lts"].version_major_minor == "24.21"


def test_a_version_is_not_extended_across_a_non_boundary():
    """3.1은 3.14.7의 앞부분이지만 같은 줄기가 아니다."""
    result = resolve("python", {"3.1": TRIXIE, "3.14.7": TRIXIE})

    assert result.values["3.1"].language_version == "3.1"


def test_a_java8_version_extends_through_the_update_and_build_numbers():
    result = resolve(
        "eclipse-temurin", {"8-jdk": TRIXIE, "8u502-jdk": TRIXIE, "8u502-b07-jdk": TRIXIE}
    )

    assert result.values["8-jdk"].language_version == "8u502-b07"
    assert result.values["8-jdk"].version_major_minor == "8"


def test_versions_that_do_not_form_one_chain_are_not_extended():
    """3.14.7과 3.14.6이 같은 이미지라고 적혀 있으면 어느 쪽이 맞는지 모른다."""
    result = resolve("python", {"3.14": TRIXIE, "3.14.7": TRIXIE, "3.14.6": TRIXIE})

    assert result.values["3.14"].language_version == "3.14"
    assert result.conflict_fields == 0


def test_a_tag_without_a_version_and_a_broken_chain_is_a_conflict():
    result = resolve("python", {"latest": TRIXIE, "3.14.7": TRIXIE, "3.14.6": TRIXIE})

    assert result.values["latest"].language_version is None
    assert result.values["latest"].version_major_minor is None
    assert result.conflict_fields == 1
    assert result.conflict_tags == 1


def test_a_value_from_the_name_is_never_overwritten():
    """이름에 alpine이 있으면 같은 지문의 다른 태그가 무엇이라 적혀 있든 alpine이다."""
    result = resolve("python", {"3.14-alpine": TRIXIE, "3.14-trixie": TRIXIE})

    assert result.values["3.14-alpine"].distribution == "alpine"


def test_a_codename_comes_only_from_tags_of_the_same_distribution():
    """3.14-alpine이 같은 지문의 3.14-trixie에서 trixie를 가져가면 alpine trixie가 된다.

    alpine 코드네임을 적은 태그가 없으니 채울 값이 없을 뿐, 값이 엇갈린 것은 아니다.
    """
    result = resolve("python", {"3.14-alpine": TRIXIE, "3.14-trixie": TRIXIE})

    assert result.values["3.14-alpine"] == Derived("3.14", "3.14", "alpine", None, "full")
    assert result.values["3.14-trixie"] == Derived("3.14", "3.14", "debian", "trixie", "full")
    assert (result.conflict_fields, result.conflict_tags) == (0, 0)


def test_a_codename_of_the_same_distribution_is_inherited():
    result = resolve(
        "python", {"3.14-alpine": TRIXIE, "3.14-alpine3.24": TRIXIE, "3.14-trixie": TRIXIE}
    )

    assert result.values["3.14-alpine"].distro_codename == "3.24"
    assert result.conflict_fields == 0


def test_a_variant_is_inherited_when_the_image_names_exactly_one():
    """eclipse-temurin:21은 이름에 변형이 없지만 21-jdk와 같은 이미지다."""
    result = resolve("eclipse-temurin", {"21": TRIXIE, "21-jdk": TRIXIE})

    assert result.values["21"].variant == "jdk"


def test_full_means_no_variant_information_at_all():
    result = resolve("python", {"3.14": TRIXIE, "3.14-trixie": TRIXIE}, lonely=("3.13",))

    assert result.values["3.14"].variant == "full"
    assert result.values["3.13"].variant == "full"


def test_conflicting_variants_leave_the_alias_empty_and_count_one_field():
    result = resolve("eclipse-temurin", {"21": TRIXIE, "21-jdk": TRIXIE, "21-jre": TRIXIE})

    assert result.values["21"].variant is None
    assert result.values["21-jdk"].variant == "jdk"
    assert result.conflict_fields == 1
    assert result.conflict_tags == 1


def test_conflicts_are_counted_per_field_and_per_tag():
    """두 별칭이 배포판과 변형에서 모두 엇갈리면 칸 4개, 태그 2개다.

    배포판을 모르면 코드네임은 가져오지 않는다. 그 칸은 엇갈림이 아니라 정보 없음이다.
    """
    result = resolve(
        "eclipse-temurin",
        {"21": TRIXIE, "latest": TRIXIE, "21-jdk-noble": TRIXIE, "21-jre-alpine3.24": TRIXIE},
    )

    assert result.values["21"] == Derived("21", "21", None, None, None)
    assert result.values["latest"].distribution is None
    assert result.values["latest"].variant is None
    assert result.conflict_fields == 4
    assert result.conflict_tags == 2


def test_tags_with_different_fingerprints_are_not_merged():
    result = resolve("python", {"3.14": TRIXIE, "3.14-alpine": ALPINE})

    assert result.values["3.14"].distribution is None
    assert result.conflict_fields == 0


def test_a_tag_without_a_fingerprint_keeps_only_what_its_name_says():
    """Windows 전용 태그와 플랫폼 정보가 없는 오래된 태그는 묶지 않는다."""
    result = resolve("python", {"3.14-trixie": TRIXIE}, lonely=("3.14",))

    assert result.values["3.14"] == Derived("3.14", "3.14", None, None, "full")


def test_the_os_repository_rule_survives_a_date_only_tag():
    result = resolve("alpine", {}, lonely=("20260805",))

    assert result.values["20260805"] == Derived(None, None, "alpine", None, "full")
