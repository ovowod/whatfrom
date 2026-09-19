# src/whatfrom/recommend/planner.py
"""질문에서 검색 조건(SearchPlan)을 뽑는다. LLM #1.

뽑은 조건은 답이 아니라 검색 조건이다. 최종 추천은 여전히 후보 안에서만 고른다.
"""

from collections.abc import Sequence

from whatfrom.core.contracts import SearchPlan
from whatfrom.recommend.llm import LLMProvider

PLAN_SYSTEM = """\
You turn a container base-image question into search conditions. Output JSON only.

Rules:
- Fill a field only when the question states it. Never add a condition from your own \
knowledge of the images. A condition the user did not state removes correct answers.
- A question that only asks whether something is fine ("is alpine okay?") states no condition.
- Tags the user only compares or asks about ("python:3.14 vs python:3.14-slim") are not \
requirements. Do not turn their versions or variants into conditions.
- repository: the image the answer must come from, chosen from the given list. \
Leave it null if the question does not point to one.
- version_prefix: a version the user requires, as written ("3.12", "17", "3.14.6"). \
When the answer is the ubuntu image itself, write its required release number here ("24.04"). \
Tag aliases such as stable, mainline, latest and lts are not versions; leave version_prefix null.
- architectures: architectures the image must run on, in Docker names (amd64, arm64).
- distributions: distributions or codenames the image must be one of \
(debian, alpine, ubuntu, bookworm, trixie, jammy, noble). \
When Ubuntu is the base OS of another image, write the release as a codename: \
22.04 -> jammy, 24.04 -> noble. Do not add a codename when the answer is the ubuntu image itself.
- exclude_distributions: distributions or codenames the image must not be.
- If the question both requires and excludes, keep both.
- max_size_mb: only when the question gives a number. "As small as possible" is not a number.
"""

# LLM이 흔히 내는 다른 이름을 Docker 표기로 맞춘다.
ARCHITECTURE_ALIASES = {
    "aarch64": "arm64",
    "arm64/v8": "arm64",
    "arm64v8": "arm64",
    "x86_64": "amd64",
    "x86-64": "amd64",
    "x64": "amd64",
}


def build_plan_prompt(question: str, repositories: Sequence[str]) -> str:
    return f"Question: {question}\n\nRepositories you may choose from: {', '.join(repositories)}"


def normalize_plan(plan: SearchPlan, repositories: Sequence[str]) -> SearchPlan:
    """LLM에 맡기지 않을 정리. 목록 밖 리포지토리는 버리고 표기를 맞춘다.

    지정과 제외가 겹쳐도 지우지 않는다. 거르기에서 중복은 해가 없고, 코드네임이
    어느 배포판에 속하는지 알아야 중복을 판정할 수 있는데 그 표는 collect에 있다.
    """
    # 수집 목록은 소문자다. LLM이 "Python"처럼 표기만 다르게 내도 버리지 않는다.
    named = (plan.repository or "").strip().lower()
    repository = named if named in set(repositories) else None

    def lower_unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(v.strip().lower() for v in values if v.strip()))

    architectures = lower_unique(plan.architectures)
    return plan.model_copy(
        update={
            "repository": repository,
            "version_prefix": (plan.version_prefix or "").strip() or None,
            "architectures": list(
                dict.fromkeys(ARCHITECTURE_ALIASES.get(a, a) for a in architectures)
            ),
            "distributions": lower_unique(plan.distributions),
            "exclude_distributions": lower_unique(plan.exclude_distributions),
        }
    )


def extract_plan(provider: LLMProvider, question: str, repositories: Sequence[str]) -> SearchPlan:
    """RemoteCallError는 그대로 올린다. 호출자가 벡터 검색만으로 계속한다."""
    plan = provider.plan(PLAN_SYSTEM, build_plan_prompt(question, repositories))
    return normalize_plan(plan, repositories)
