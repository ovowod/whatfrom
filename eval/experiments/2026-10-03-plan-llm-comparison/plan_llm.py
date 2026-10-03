"""M1-a(조건 추출 단계의 LLM 비교)의 측정 설정과 집계 규칙.

네트워크와 DB를 쓰지 않는다. 결과 JSON과 repository 목록 기록만 받아 비교 표와
고른 측정 설정을 낸다. 규칙의 근거는 .scratch/plan-llm-comparison/spec.md에 있다.
"""

import math
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class Config:
    """측정 설정 하나. 가격은 1M token당 USD다(조사 노트, 접근일 2026-10-03)."""

    name: str
    base_url: str
    model: str
    extra_body: dict | None
    key_env: str
    mode: str
    input_price: float
    output_price: float
    # 과금 출력 token을 usage에서 읽는 규칙. None이면 규칙을 모르므로 비용도 알 수 없다.
    output_rule: str | None


# OpenAI·Moonshot의 completion_tokens는 reasoning token을 포함한다.
COMPLETION = "completion"
# xAI의 completion_tokens는 reasoning token을 빼고 센다(REST reference의 usage 예시).
COMPLETION_PLUS_REASONING = "completion+reasoning"


KIMI = "https://api.moonshot.ai/v1"

CONFIGS = {
    config.name: config
    for config in (
        # 기준선. 운영과 같게 reasoning_effort를 보내지 않는다(기본값 max).
        Config(
            name="kimi-max",
            base_url=KIMI,
            model="kimi-k3",
            extra_body=None,
            key_env="MOONSHOT_API_KEY",
            mode="full",
            input_price=3.00,
            output_price=15.00,
            output_rule=COMPLETION,
        ),
        Config(
            name="kimi-low",
            base_url=KIMI,
            model="kimi-k3",
            extra_body={"reasoning_effort": "low"},
            key_env="MOONSHOT_API_KEY",
            mode="plan-only",
            input_price=3.00,
            output_price=15.00,
            output_rule=COMPLETION,
        ),
        Config(
            name="luna-none",
            base_url="https://api.openai.com/v1",
            model="gpt-6-luna",
            extra_body={"reasoning_effort": "none"},
            key_env="OPENAI_API_KEY",
            mode="plan-only",
            input_price=0.10,
            output_price=0.50,
            output_rule=COMPLETION,
        ),
        # luna-low와 sol-low는 1회차 결과를 본 뒤 추가했다. luna-none이 하한에 못 미쳤고,
        # 고른 kimi-low보다 훨씬 싼 GPT에 reasoning을 조금 준 설정이 비교 대상에 없었다.
        Config(
            name="luna-low",
            base_url="https://api.openai.com/v1",
            model="gpt-6-luna",
            extra_body={"reasoning_effort": "low"},
            key_env="OPENAI_API_KEY",
            mode="plan-only",
            input_price=0.10,
            output_price=0.50,
            output_rule=COMPLETION,
        ),
        # none과 minimal을 지원하지 않아 low가 가장 낮은 수준이다.
        Config(
            name="sol-low",
            base_url="https://api.openai.com/v1",
            model="gpt-6.1-sol",
            extra_body={"reasoning_effort": "low"},
            key_env="OPENAI_API_KEY",
            mode="plan-only",
            input_price=2.00,
            output_price=10.00,
            output_rule=COMPLETION,
        ),
        Config(
            name="grok-none",
            base_url="https://api.x.ai/v1",
            model="grok-4.3",
            extra_body={"reasoning_effort": "none"},
            key_env="XAI_API_KEY",
            mode="plan-only",
            input_price=1.25,
            output_price=2.50,
            output_rule=COMPLETION_PLUS_REASONING,
        ),
        Config(
            name="gemini-minimal",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-3.5-flash-lite",
            # Gemini 고유 설정은 요청 JSON 안에 "extra_body" key를 글자 그대로 넣는다.
            extra_body={
                "extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}
            },
            key_env="GEMINI_API_KEY",
            mode="plan-only",
            input_price=0.30,
            output_price=2.50,
            # 문서에 OpenAI 호환 usage 형식이 없다. 사전 확인의 원본 usage로 판별되면 정한다.
            output_rule=None,
        ),
    )
}


BASELINE = "kimi-max"
# 하한보다 이만큼 넘게 떨어지면 탈락이고, 하한에서 이만큼 안이면 경계라 한 번 더 돌린다.
MARGIN = 2
# 가장 빠른 p95와의 차이가 기준선 요청 전체 p95의 이 비율 안이면 성능이 비슷한 것으로 본다.
# 단계 지연 대비 비율로 보면 짧은 단계에서 사용자가 느끼지 못할 차이로도 갈린다.
SIMILAR_LATENCY = 0.05
# 가장 싼 비용의 이 배수 안이면 비용이 같은 것으로 보고 정확도로 고른다.
SIMILAR_COST = 1.10
# 재시도가 섞인 회차만 있는 설정을 다시 돌리는 최대 횟수.
MAX_CONFIRMATIONS = 2


class InvalidResults(Exception):
    """비교할 수 없는 결과가 섞였다. 표를 만들지 않는다."""


@dataclass(frozen=True)
class Run:
    """측정 설정 하나의 한 회차 결과 JSON."""

    config: str
    round: int
    document: dict


def stage_meta(config: Config) -> dict:
    """결과 meta의 llm_stages에 남는 값과 같은 꼴."""
    return {"base_url": config.base_url, "model": config.model, "extra_body": config.extra_body}


def validate(run: Run, case_ids: list[str]) -> None:
    """측정 설정과 다르게 돈 결과면 InvalidResults를 낸다.

    fake provider로 돈 결과나 다른 설정으로 돈 결과가 모델 이름 아래 섞이면 비교가 무의미하다.
    """
    where = f"{run.config} {run.round}회차"
    config = CONFIGS.get(run.config)
    if config is None:
        raise InvalidResults(f"{where}: 모르는 측정 설정이다")
    meta = run.document.get("meta", {})
    if meta.get("llm_provider") != "openai_compatible":
        raise InvalidResults(f"{where}: llm_provider가 {meta.get('llm_provider')!r}다")
    if meta.get("mode") != config.mode:
        raise InvalidResults(f"{where}: mode가 {meta.get('mode')!r}다. {config.mode!r}여야 한다")
    stages = meta.get("llm_stages") or {}
    if stages.get("plan") != stage_meta(config):
        raise InvalidResults(f"{where}: 조건 추출 단계 설정이 측정 설정과 다르다")
    # 기준선은 운영과 같은 kimi-k3로 추천 단계까지 돈다.
    if config.mode == "full" and stages.get("recommend") != stage_meta(config):
        raise InvalidResults(f"{where}: 추천 단계 설정이 운영 설정과 다르다")
    # fake embedder면 검색 결과가 달라져 추천 단계 prompt와 지연이 운영과 달라진다.
    if config.mode == "full" and meta.get("embedder") != "openai_compatible":
        raise InvalidResults(f"{where}: embedder가 {meta.get('embedder')!r}다")
    # 정확도 하한은 기준선 1회차로 정한다. 다시 돌리면 하한이 움직인다.
    if run.config == BASELINE and run.round != 1:
        raise InvalidResults(f"{where}: 기준선은 다시 돌리지 않는다")
    if run.document.get("skipped"):
        raise InvalidResults(f"{where}: 미측정 문항이 있다")
    measured = sorted(case["case_id"] for case in run.document.get("cases", []))
    if measured != sorted(case_ids):
        raise InvalidResults(f"{where}: 측정한 문항이 golden set 문항과 다르다")


@dataclass(frozen=True)
class Row:
    """측정 설정 하나의 비교 표 행. 일치율은 회차 평균이다.

    지연은 재시도가 없는 회차의 문항만 합쳐 계산한다. 그런 회차가 없으면 모든 회차로
    계산하되 latency_trusted가 False다.
    """

    config: str
    rounds: int
    # 1회차의 (repository 추출 일치, 조건 추출 일치) 문항 수. 경계인지 이것으로 판단한다.
    first_round: tuple[int, int]
    repository: float
    plan: float
    p50: float
    p95: float
    # 요청 전체 소요 시간의 p95. 추천 단계까지 도는 기준선(전체 평가)만 있다.
    request_p95: float | None
    # 회차마다 조건 추출 단계 호출의 시도 횟수가 1보다 큰 문항 수.
    retried_cases: list[int]
    latency_trusted: bool
    # token과 비용은 회차 평균이라 늘 golden set 한 벌 기준이다. 모르면 None이다.
    input_tokens: float | None
    output_tokens: float | None
    # reasoning token을 보고하지 않는 공급자(Moonshot)는 None이다.
    reasoning_tokens: float | None
    cost: float | None
    # token 수가 있는 조건 추출 단계 호출의 비율.
    usage_coverage: float
    # 실패한 호출 수. 오류 문장의 ":" 앞부분으로 묶는다.
    failures: dict[str, int]


@dataclass(frozen=True)
class Outcome:
    """kind는 다음 중 하나다.

    - chosen: config를 고른다.
    - incomplete: 기준선이 없어 고를 수 없다. M1-a는 미완료다.
    - needs_runs: 고르기 전에 더 돌려야 한다. reasons에 무엇을 돌릴지 적는다.
    - inconclusive: 동점을 가를 수 없다.
    - keep_kimi: 고를 수 있는 측정 설정이 남지 않아 kimi-k3(운영 설정)를 유지한다.
    """

    kind: str
    config: str | None
    reasons: list[str]


@dataclass(frozen=True)
class Summary:
    rows: dict[str, Row]
    outcome: Outcome
    # 실험 전체에 쓴 비용. 모든 회차와 기준선의 추천 단계 호출까지 더한다. 하나라도 모르면 None이다.
    total_cost: float | None


def nearest_rank(values: list[float], q: float) -> float:
    """값을 작은 순으로 놓고 ⌈q·n⌉번째 값. 보간하지 않는다."""
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)), 1) - 1]


def plan_calls(case: dict) -> list[dict]:
    """문항의 조건 추출 단계 호출 기록. 기준선은 전체 평가라 추천 단계 기록이 섞여 있다."""
    return [call for call in case.get("llm_calls", []) if call["stage"] == "plan"]


def count_retried_cases(run: Run) -> int:
    return sum(
        any(call["attempts"] > 1 for call in plan_calls(case)) for case in run.document["cases"]
    )


def billed_output(call: dict, rule: str | None) -> int | None:
    if rule is None or call["output_tokens"] is None:
        return None
    if rule == COMPLETION_PLUS_REASONING:
        if call["reasoning_tokens"] is None:
            return None
        return call["output_tokens"] + call["reasoning_tokens"]
    return call["output_tokens"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost: float | None


def _sum_or_none(values: list) -> int | None:
    return None if None in values else sum(values)


def usage_of(calls: list[dict], config: Config) -> Usage:
    """호출 기록의 token 합과 비용. 하나라도 모르는 호출이 있으면 그 값은 None이다.

    모르는 값을 0이나 추정치로 채우면 그 모델이 실제보다 싸 보인다.
    """
    input_total = _sum_or_none([call["input_tokens"] for call in calls])
    output_total = _sum_or_none([billed_output(call, config.output_rule) for call in calls])
    cost = (
        None
        if input_total is None or output_total is None
        else (input_total * config.input_price + output_total * config.output_price) / 1_000_000
    )
    reasoning_total = _sum_or_none([call["reasoning_tokens"] for call in calls])
    return Usage(input_total, output_total, reasoning_total, cost)


def all_calls(run: Run) -> list[dict]:
    return [call for case in run.document["cases"] for call in case.get("llm_calls", [])]


def _mean_or_none(values: list) -> float | None:
    return None if None in values else mean(values)


def _row(config: str, runs: list[Run]) -> Row:
    retried = [count_retried_cases(run) for run in runs]
    clean = [run for run, count in zip(runs, retried, strict=True) if count == 0]
    seconds = [case["seconds_plan"] for run in clean or runs for case in run.document["cases"]]
    per_round = [run.document["cases"] for run in runs]
    usages = [
        usage_of([call for case in cases for call in plan_calls(case)], CONFIGS[config])
        for cases in per_round
    ]
    calls = [call for cases in per_round for case in cases for call in plan_calls(case)]
    reported = [call for call in calls if call["input_tokens"] is not None]
    failures: dict[str, int] = {}
    for call in calls:
        if not call["ok"]:
            reason = (call["error"] or "").split(":")[0]
            failures[reason] = failures.get(reason, 0) + 1
    first = runs[0].document["cases"]
    return Row(
        config=config,
        rounds=len(runs),
        first_round=(
            sum(c["repository_extracted"] for c in first),
            sum(c["plan_matched"] for c in first),
        ),
        repository=mean(sum(c["repository_extracted"] for c in cases) for cases in per_round),
        plan=mean(sum(c["plan_matched"] for c in cases) for cases in per_round),
        p50=nearest_rank(seconds, 0.50),
        p95=nearest_rank(seconds, 0.95),
        request_p95=(
            nearest_rank([c["seconds_total"] for cases in per_round for c in cases], 0.95)
            if CONFIGS[config].mode == "full"
            else None
        ),
        retried_cases=retried,
        latency_trusted=bool(clean),
        input_tokens=_mean_or_none([usage.input_tokens for usage in usages]),
        output_tokens=_mean_or_none([usage.output_tokens for usage in usages]),
        reasoning_tokens=_mean_or_none([usage.reasoning_tokens for usage in usages]),
        cost=_mean_or_none([usage.cost for usage in usages]),
        usage_coverage=len(reported) / len(calls) if calls else 0.0,
        failures=failures,
    )


def summarize(runs: list[Run], case_ids: list[str], repositories: tuple[dict, dict]) -> Summary:
    """repositories는 측정 전과 후에 기록한 {repository: 색인 문서 수}다."""
    before, after = repositories
    if before != after:
        raise InvalidResults("측정 전후 repository 목록이 다르다. 이번 측정 전체가 무효다")
    for run in runs:
        validate(run, case_ids)
    hashes = {run.document["meta"].get("goldenset_sha256") for run in runs}
    if len(hashes) > 1:
        raise InvalidResults("결과마다 golden set hash가 다르다")

    by_config: dict[str, list[Run]] = {}
    for run in sorted(runs, key=lambda r: (r.config, r.round)):
        by_config.setdefault(run.config, []).append(run)
    rows = {name: _row(name, config_runs) for name, config_runs in by_config.items()}
    # 기준선의 추천 단계 호출도 kimi-k3라 같은 단가로 더한다.
    costs = [usage_of(all_calls(run), CONFIGS[run.config]).cost for run in runs]
    return Summary(
        rows=rows, outcome=choose(rows), total_cost=None if None in costs else sum(costs)
    )


def choose(rows: dict[str, Row]) -> Outcome:
    """spec §고르는 기준. rows의 순서와 무관하게 같은 답을 낸다."""
    baseline = rows.get(BASELINE)
    if baseline is None:
        return Outcome("incomplete", None, ["kimi-max 기준선이 없어 정확도 하한을 정할 수 없다"])
    floor = tuple(hits - MARGIN for hits in baseline.first_round)

    def near_floor(row: Row) -> bool:
        return row.config != BASELINE and any(
            abs(hits - limit) <= MARGIN for hits, limit in zip(row.first_round, floor, strict=True)
        )

    pending = [
        f"{row.config}: 1회차가 하한 ±{MARGIN}문항 안이라 한 번 더 돌린다"
        for row in sorted(rows.values(), key=lambda r: r.config)
        if near_floor(row) and row.rounds < 2
    ]
    if pending:
        return Outcome("needs_runs", None, pending)

    pool = sorted(
        (row for row in rows.values() if _passes(row, floor)),
        key=lambda r: r.config,
    )
    reasons: list[str] = []
    # 기준선은 전체 평가라 request_p95가 늘 있다.
    similar = baseline.request_p95 * SIMILAR_LATENCY
    while pool:
        fastest = min(row.p95 for row in pool)
        group = [row for row in pool if row.p95 <= fastest + similar]
        top = group
        if len(group) > 1:
            if any(row.cost is None for row in group):
                names = ", ".join(row.config for row in group)
                return Outcome("inconclusive", None, [f"{names}: 비용을 몰라 비교할 수 없다"])
            cheapest = min(row.cost for row in group)
            near = [row for row in group if row.cost <= cheapest * SIMILAR_COST]
            best = max(row.repository + row.plan for row in near)
            top = [row for row in near if row.repository + row.plan == best]
            if len(top) > 1:
                names = ", ".join(row.config for row in top)
                return Outcome("inconclusive", None, [f"{names}: 성능, 비용, 정확도가 모두 같다"])
        winner = top[0]
        if winner.latency_trusted:
            return Outcome("chosen", winner.config, reasons)
        if winner.config == BASELINE:
            # 기준선은 다시 돌리지 않으므로 확인 회차가 없다.
            reasons.append(f"{BASELINE}: 재시도가 섞여 지연을 판정할 수 없어 뺐다")
            pool = [row for row in pool if row is not winner]
            continue
        planned = 2 if near_floor(winner) else 1
        if winner.rounds - planned < MAX_CONFIRMATIONS:
            return Outcome(
                "needs_runs", None, [f"{winner.config}: 재시도 없는 확인 회차가 필요하다"]
            )
        reasons.append(f"{winner.config}: 확인 회차 {MAX_CONFIRMATIONS}번에도 재시도가 섞여 뺐다")
        pool = [row for row in pool if row is not winner]
    return Outcome(
        "keep_kimi", None, [*reasons, "고를 수 있는 측정 설정이 남지 않아 kimi-k3를 유지한다"]
    )


def _passes(row: Row, floor: tuple[int, ...]) -> bool:
    return row.repository >= floor[0] and row.plan >= floor[1]


def _number(value: float | None, digits: int = 1, unknown: str = "알 수 없음") -> str:
    return unknown if value is None else f"{value:,.{digits}f}"


def render(summary: Summary) -> str:
    """비교 표와 결론을 Markdown으로."""
    lines = [
        "| 측정 설정 | 회차 | repository 추출 | 조건 추출 | p50(초) | p95(초) "
        "| 재시도 문항 | 실패 호출 | 입력 token | 출력 token | reasoning token "
        "| usage 보고율 | 40문항 비용(USD) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in CONFIGS:
        row = summary.rows.get(name)
        if row is None:
            continue
        latency_note = "" if row.latency_trusted else " (재시도 섞임)"
        failures = ", ".join(f"{reason} {count}" for reason, count in row.failures.items())
        retried = ", ".join(
            f"{count} (재시도 섞임)" if count else "0" for count in row.retried_cases
        )
        lines.append(
            f"| {name} | {row.rounds} | {row.repository:g} | {row.plan:g} "
            f"| {row.p50:.1f}{latency_note} | {row.p95:.1f}{latency_note} "
            f"| {retried} | {failures or '-'} "
            f"| {_number(row.input_tokens, 0)} | {_number(row.output_tokens, 0)} "
            f"| {_number(row.reasoning_tokens, 0, unknown='-')} "
            f"| {row.usage_coverage:.0%} | {_number(row.cost, 4)} |"
        )
    outcome = summary.outcome
    baseline = summary.rows.get(BASELINE)
    if baseline is not None and baseline.request_p95 is not None:
        lines += [
            "",
            f"기준선 요청 전체 p95: {baseline.request_p95:.1f}초. 가장 빠른 p95에서 "
            f"{baseline.request_p95 * SIMILAR_LATENCY:.1f}초 안이면 성능이 비슷한 것으로 본다",
        ]
    lines += [
        "",
        f"실험 전체 비용: {_number(summary.total_cost, 4)} USD",
        "",
        f"결론: {outcome.kind}" + (f" ({outcome.config})" if outcome.config else ""),
        *[f"- {reason}" for reason in outcome.reasons],
    ]
    return "\n".join(lines)
