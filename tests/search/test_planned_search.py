# tests/search/test_planned_search.py
"""검색 조건으로 후보를 만드는 경로: 리포지토리 강제, 전체 기준 완화, 알림."""

from whatfrom.core.contracts import SearchPlan
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.models import Repository
from whatfrom.search.retrieval import search_candidates_by_vector, search_candidates_with_plan

from .plan_seed import DAY, NOW, add_repository, add_tag

PY_README = "# Image Variants\n\npython slim alpine bookworm trixie images.\n"
JAVA_README = "# How to use this Image\n\njava jdk jre temurin gradle build.\n"
ALPINE_README = "# What is Alpine Linux?\n\nminimal musl busybox base image.\n"


def vector(text: str) -> list[float]:
    return FakeEmbedder().embed([text])[0]


def seed(session) -> None:
    add_repository(session, "py", PY_README)
    add_repository(session, "tem", JAVA_README)
    add_repository(session, "alp", ALPINE_README)
    add_tag(
        session, "py", "3.14-trixie", version="3.14.7", distribution="debian", codename="trixie"
    )
    add_tag(
        session, "py", "3.14-bookworm", version="3.14.7", distribution="debian", codename="bookworm"
    )
    add_tag(
        session, "py", "3.13-trixie", version="3.13.9", distribution="debian", codename="trixie"
    )
    add_tag(
        session, "tem", "17-jdk-jammy", version="17.0.20_8", distribution="ubuntu", codename="jammy"
    )
    add_tag(
        session, "tem", "17-jdk-noble", version="17.0.20_8", distribution="ubuntu", codename="noble"
    )
    add_tag(
        session, "tem", "21-jdk-noble", version="21.0.12_8", distribution="ubuntu", codename="noble"
    )
    add_tag(session, "alp", "3.24", version="3.24.1", distribution="alpine", codename="3.24")


def images(result) -> list[str]:
    return [c.image for c in result.candidates]


def test_an_empty_plan_gives_the_same_candidates_as_the_vector_path(session):
    """조건이 없으면 거르지 않는다. 기존 경로와 결과가 같아야 한다."""
    seed(session)
    v = vector("python slim alpine bookworm")

    result = search_candidates_with_plan(session, v, SearchPlan())

    assert images(result) == [c.image for c in search_candidates_by_vector(session, v)]
    assert (result.notes, result.degraded) == ([], False)


def test_the_plan_repository_is_forced_even_when_the_vector_search_misses_it(session):
    """ "Java 17"인데 검색이 temurin 문서를 못 찾은 경우. 지정 리포지토리만 후보를 낸다."""
    seed(session)
    v = vector("python slim alpine bookworm")

    result = search_candidates_with_plan(
        session, v, SearchPlan(repository="tem", version_prefix="17"), chunk_k=1
    )

    assert images(result) == ["tem:17-jdk-jammy", "tem:17-jdk-noble"]
    assert all(c.evidence for c in result.candidates)
    assert result.candidates[0].evidence[0].section_title == "How to use this Image"
    assert (result.notes, result.degraded) == ([], False)


def test_a_repository_without_documents_is_not_forced(session):
    """색인되지 않은 리포지토리는 근거가 없다.

    벡터 검색 리포지토리로 돌아가고 버전은 쓰지 않는다.
    """
    seed(session)
    # 태그만 있고 문서가 없는 리포지토리.
    session.add(
        Repository(name="ghost", is_official=True, source_url="https://x.invalid", collected_at=NOW)
    )
    session.flush()
    add_tag(session, "ghost", "1.0")
    v = vector("python slim alpine bookworm")

    result = search_candidates_with_plan(
        session, v, SearchPlan(repository="ghost", version_prefix="1.0"), chunk_k=1
    )

    assert images(result) and all(i.startswith("py:") for i in images(result))
    assert result.degraded is True
    assert any("ghost의 문서나 태그가 없어" in note for note in result.notes)
    assert any("버전 조건(1.0)을 쓰지 않았습니다" in note for note in result.notes)


def test_a_repository_without_tags_is_not_forced(session):
    """문서가 있어도 태그가 없으면 후보를 낼 수 없어 강제하지 않는다."""
    seed(session)
    add_repository(session, "empty", "# Image Variants\n\npython slim alpine bookworm.\n")
    v = vector("python slim alpine bookworm")

    result = search_candidates_with_plan(session, v, SearchPlan(repository="empty"))

    assert images(result)
    assert result.degraded is True
    assert any("empty의 문서나 태그가 없어" in note for note in result.notes)


def test_a_version_without_a_repository_is_not_applied(session):
    seed(session)

    result = search_candidates_with_plan(
        session, vector("python slim alpine bookworm"), SearchPlan(version_prefix="3.13"), chunk_k=1
    )

    assert "py:3.14-trixie" in images(result)
    assert result.notes == ["리포지토리를 특정하지 못해 버전 조건(3.13)을 쓰지 않았습니다."]
    assert result.degraded is False


def test_repositories_that_fail_the_conditions_give_no_candidates(session):
    """조건을 만족하는 python 후보가 있으면 alpine 리포지토리는 완화하지 않고 빠진다."""
    seed(session)
    v = vector("python slim alpine bookworm musl busybox base image")

    result = search_candidates_with_plan(session, v, SearchPlan(distributions=["bookworm"]))

    assert images(result) == ["py:3.14-bookworm"]
    assert (result.notes, result.degraded) == ([], False)


def test_conditions_are_relaxed_only_when_every_repository_is_empty(session):
    seed(session)

    result = search_candidates_with_plan(
        session,
        vector("java jdk"),
        SearchPlan(repository="tem", distributions=["bookworm"]),
    )

    assert images(result) == ["tem:21-jdk-noble", "tem:17-jdk-jammy", "tem:17-jdk-noble"]
    assert result.notes == ["조건에 맞는 태그가 없어 배포판 조건(bookworm)을 풀었습니다."]
    assert result.degraded is True


def test_a_pinned_stale_line_is_offered_with_a_note(session):
    seed(session)
    add_tag(
        session,
        "tem",
        "8-jdk-jammy",
        version="8u502-b07",
        distribution="ubuntu",
        codename="jammy",
        pushed=NOW - 300 * DAY,
    )

    result = search_candidates_with_plan(
        session, vector("java jdk"), SearchPlan(repository="tem", version_prefix="8")
    )

    assert images(result) == ["tem:8-jdk-jammy"]
    assert result.notes == [
        "tem 8 줄기는 가장 최근 줄기보다 마지막 푸시가 60일 넘게 뒤처져 있습니다."
    ]
    assert result.degraded is False


def test_a_version_that_no_tag_has_is_relaxed_last(session):
    """버전은 가장 강한 요구라 마지막에 푼다. 풀면 그렇다고 알린다."""
    seed(session)

    result = search_candidates_with_plan(
        session,
        vector("java jdk"),
        SearchPlan(repository="tem", version_prefix="99", distributions=["bookworm"]),
    )

    assert images(result) == ["tem:21-jdk-noble", "tem:17-jdk-jammy", "tem:17-jdk-noble"]
    assert result.notes == [
        "조건에 맞는 태그가 없어 배포판 조건(bookworm), 버전 조건(99)을 풀었습니다."
    ]
    assert result.degraded is True


def test_an_exact_patch_version_in_the_database_is_not_relaxed(session):
    """3.14.6이 DB에 있으면 버전 조건을 풀지 않고 그 태그를 낸다.

    별칭 3.14는 3.14.7을 가리켜 SQL 조건을 통과하지 못하고, 이동 별칭만 고르면
    존재하는 3.14.6까지 버려 결국 버전 조건을 풀게 된다.
    """
    seed(session)
    add_tag(session, "py", "3.14.6", version="3.14.6", distribution="debian", codename="trixie")

    result = search_candidates_with_plan(
        session, vector("python slim"), SearchPlan(repository="py", version_prefix="3.14.6")
    )

    assert images(result) == ["py:3.14.6"]
    assert (result.notes, result.degraded) == ([], False)


def test_no_stale_line_note_after_the_version_was_relaxed(session):
    """8 줄기의 별칭이 모두 낡은 변형이라 고를 수 없으면 버전을 푼다.

    그 뒤의 후보에는 8 줄기가 없으므로 8 줄기가 뒤처졌다고 알리면 안 된다.
    """
    seed(session)
    add_tag(
        session,
        "tem",
        "8u502-b07-jdk-jammy",
        version="8u502-b07",
        distribution="ubuntu",
        codename="jammy",
        pushed=NOW - 200 * DAY,
    )
    add_tag(
        session,
        "tem",
        "8-jdk-jammy",
        version="8u502-b07",
        distribution="ubuntu",
        codename="jammy",
        pushed=NOW - 300 * DAY,
    )

    result = search_candidates_with_plan(
        session, vector("java jdk"), SearchPlan(repository="tem", version_prefix="8")
    )

    assert "tem:8-jdk-jammy" not in images(result)
    assert result.notes == ["조건에 맞는 태그가 없어 버전 조건(8)을 풀었습니다."]


def add_noisy_repository(session) -> None:
    """청크가 여럿인 태그 목록 섹션과 짧은 섹션을 가진 리포지토리."""
    tags = "java jdk jre temurin gradle build tag list. " * 150
    add_repository(
        session,
        "noise",
        f"# Supported tags\n\n{tags}\n\n# How to use this Image\n\njava jdk build.\n",
    )
    add_tag(session, "noise", "1.0", version="1.0", distribution="debian", codename="trixie")


def test_a_long_section_does_not_crowd_out_other_repositories(session):
    """태그 목록 섹션이 상위를 독차지하면 다른 리포지토리 후보가 사라진다."""
    seed(session)
    add_noisy_repository(session)

    result = search_candidates_with_plan(
        session, vector("java jdk jre temurin gradle build tag list"), SearchPlan()
    )

    assert len({i.split(":")[0] for i in images(result)}) > 1


def test_a_forced_repository_not_in_the_search_gets_two_sections_as_evidence(session):
    """근거를 따로 찾을 때는 서로 다른 섹션 두 개를 넘긴다."""
    seed(session)
    add_noisy_repository(session)

    result = search_candidates_with_plan(
        session, vector("python slim alpine bookworm"), SearchPlan(repository="noise"), chunk_k=1
    )

    titles = [e.section_title for e in result.candidates[0].evidence]
    assert sorted(titles) == ["How to use this Image", "Supported tags"]


def test_a_forced_repository_found_by_the_search_keeps_that_evidence(session):
    """검색에 이미 있으면 따로 찾지 않는다. 섹션이 하나뿐일 수 있다."""
    seed(session)

    result = search_candidates_with_plan(
        session, vector("java jdk build"), SearchPlan(repository="tem"), chunk_k=1
    )

    assert [e.section_title for e in result.candidates[0].evidence] == ["How to use this Image"]
