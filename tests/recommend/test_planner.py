# tests/recommend/test_planner.py
"""질문에서 검색 조건을 뽑는 LLM #1. 공급자는 fake다."""

import pytest

from whatfrom.core.contracts import SearchPlan
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.llm import FakeLLMProvider
from whatfrom.recommend.planner import PLAN_SYSTEM, build_plan_prompt, extract_plan

REPOSITORIES = ["alpine", "debian", "eclipse-temurin", "node", "python"]


def test_the_prompt_carries_the_question_and_the_repository_list():
    provider = FakeLLMProvider()

    extract_plan(provider, "Java 17 레거시 서비스야", REPOSITORIES)

    [(system, prompt)] = provider.plan_calls
    assert system == PLAN_SYSTEM
    assert "Java 17 레거시 서비스야" in prompt
    assert "eclipse-temurin" in prompt


def test_the_system_prompt_forbids_conditions_the_question_does_not_state():
    """없는 조건은 정답 후보를 지운다. 추출 원칙이 프롬프트에 있어야 한다."""
    assert "only when the question states it" in PLAN_SYSTEM
    assert "keep both" in PLAN_SYSTEM
    assert "22.04 -> jammy" in PLAN_SYSTEM


def test_the_system_prompt_separates_ubuntu_itself_from_ubuntu_as_a_base():
    """ubuntu 이미지 자체는 버전 번호로, 다른 이미지의 베이스는 코드네임으로 적는다.

    골든셋 라벨과 같은 규칙이다. 다르면 올바른 후보를 만든 추출도 불필요한
    distributions 때문에 오답으로 채점된다.
    """
    assert 'ubuntu image itself, write its required release number here ("24.04")' in PLAN_SYSTEM
    assert "Do not add a codename when the answer is the ubuntu image itself" in PLAN_SYSTEM


def test_a_repository_outside_the_list_is_dropped():
    """수집되지 않은 리포지토리는 후보를 낼 수 없다. 목록과 대조하는 것은 코드다."""
    plan = extract_plan(FakeLLMProvider(plan=SearchPlan(repository="openjdk")), "q", REPOSITORIES)

    assert plan.repository is None


def test_a_repository_in_the_list_is_kept():
    plan = extract_plan(
        FakeLLMProvider(plan=SearchPlan(repository="eclipse-temurin")), "q", REPOSITORIES
    )

    assert plan.repository == "eclipse-temurin"


def test_a_repository_is_matched_regardless_of_case_and_spaces():
    """수집 목록은 소문자다. LLM이 표기만 다르게 내도 버리지 않는다."""
    plan = extract_plan(
        FakeLLMProvider(plan=SearchPlan(repository=" Eclipse-Temurin ")), "q", REPOSITORIES
    )

    assert plan.repository == "eclipse-temurin"


def test_the_system_prompt_says_compared_tags_are_not_requirements():
    """ "python:3.14랑 3.14-slim이 뭐가 달라?"의 3.14는 비교 대상이지 요구가 아니다.

    골든셋 라벨과 같은 규칙이다.
    """
    assert "Tags the user only compares or asks about" in PLAN_SYSTEM
    assert "are not requirements" in PLAN_SYSTEM


def test_the_system_prompt_says_tag_aliases_are_not_versions():
    """nginx:stable의 stable은 브랜치 별칭이다. 버전으로 거르면 맞는 태그가 없어 조건을 푼다."""
    assert "Tag aliases such as stable, mainline, latest and lts are not versions" in PLAN_SYSTEM


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (["aarch64"], ["arm64"]),
        (["ARM64"], ["arm64"]),
        (["arm64/v8", "arm64"], ["arm64"]),
        (["x86_64"], ["amd64"]),
        (["amd64", "arm64"], ["amd64", "arm64"]),
    ],
)
def test_architecture_names_are_normalized_to_docker_names(given, expected):
    plan = extract_plan(FakeLLMProvider(plan=SearchPlan(architectures=given)), "q", REPOSITORIES)

    assert plan.architectures == expected


def test_distribution_values_are_lowercased():
    plan = extract_plan(
        FakeLLMProvider(
            plan=SearchPlan(distributions=["Bookworm"], exclude_distributions=["Alpine"])
        ),
        "q",
        REPOSITORIES,
    )

    assert (plan.distributions, plan.exclude_distributions) == (["bookworm"], ["alpine"])


def test_required_and_excluded_distributions_are_both_kept():
    """ "Debian이어야 하지만 trixie는 안 된다"는 두 조건 모두 필요하다."""
    plan = extract_plan(
        FakeLLMProvider(
            plan=SearchPlan(distributions=["debian"], exclude_distributions=["trixie"])
        ),
        "q",
        REPOSITORIES,
    )

    assert (plan.distributions, plan.exclude_distributions) == (["debian"], ["trixie"])


def test_a_blank_version_prefix_becomes_none():
    plan = extract_plan(FakeLLMProvider(plan=SearchPlan(version_prefix="  ")), "q", REPOSITORIES)

    assert plan.version_prefix is None


def test_a_provider_failure_propagates():
    """실패를 삼키지 않는다. 호출자가 벡터 검색만으로 계속하고 그렇다고 알린다."""
    with pytest.raises(RemoteCallError):
        extract_plan(FakeLLMProvider(plan_error=RemoteCallError("down")), "q", REPOSITORIES)


def test_the_prompt_lists_repositories_in_the_given_order():
    assert build_plan_prompt("q", ["b", "a"]).endswith("b, a")
