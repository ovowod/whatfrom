"""M1-b(추천 단계의 LLM 비교)의 측정 설정, 결과 검증, 판정 규칙.

네트워크와 DB를 쓰지 않는다. 결과 JSON, 고정 입력의 출처, 회차 전후 snapshot만 받아
비교 표와 고른 측정 설정을 낸다. 규칙의 근거는 .scratch/recommend-llm-comparison/spec.md에 있다.
"""

import math
from dataclasses import dataclass, replace
from statistics import mean

# 고정 입력의 출처. M1-a에서 실제 embedder로 다시 잰 kimi-k3 전체 평가다.
REFERENCE_NAME = "kimi-max-r1.json"


@dataclass(frozen=True)
class Config:
    """측정 설정 하나. 가격은 1M token당 USD다(조사 노트, 접근일 2026-10-03)."""

    name: str
    api: str
    base_url: str
    model: str
    extra_body: dict | None
    key_env: str
    input_price: float
    output_price: float
    # 과금 출력 token을 usage에서 읽는 규칙. None이면 규칙을 모르므로 비용도 알 수 없다.
    output_rule: str | None
    # 문항 사이에 쉴 초. 분 단위 rate limit에 걸린 공급자만 준다.
    interval_seconds: float = 0.0


# OpenAI·Moonshot의 completion_tokens와 Anthropic의 output_tokens는 reasoning token을 포함한다.
COMPLETION = "completion"
# xAI의 completion_tokens는 reasoning token을 빼고 센다(REST reference의 usage 예시).
COMPLETION_PLUS_REASONING = "completion+reasoning"

KIMI = "https://api.moonshot.ai/v1"
OPENAI = "https://api.openai.com/v1"
XAI = "https://api.x.ai/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/openai"
ANTHROPIC = "https://api.anthropic.com/v1"


def _gemini_thinking(level: str) -> dict:
    # Gemini 고유 설정은 요청 JSON 안에 "extra_body" key를 글자 그대로 넣는다.
    return {"extra_body": {"google": {"thinking_config": {"thinking_level": level}}}}


def _config(name: str, api: str, base_url: str, model: str, extra_body: dict | None) -> Config:
    """모델마다 같은 key, 가격, token 규칙, 문항 사이 대기를 쓴다."""
    by_model = {
        "kimi-k3": ("MOONSHOT_API_KEY", 3.00, 15.00, COMPLETION, 0.0),
        "gpt-6-luna": ("OPENAI_API_KEY", 0.10, 0.50, COMPLETION, 0.0),
        "gpt-6.1-sol": ("OPENAI_API_KEY", 2.00, 10.00, COMPLETION, 0.0),
        "grok-4.3": ("XAI_API_KEY", 1.25, 2.50, COMPLETION_PLUS_REASONING, 0.0),
        # 문서에 OpenAI 호환 usage 형식이 없다. M1-b 사전 확인(2026-10-04)의 원본 usage에서
        # total_tokens가 prompt_tokens + completion_tokens와 같아, 과금 출력이 모두
        # completion_tokens에 있다고 본다. thinking이 따로 과금된다면 total이 그만큼 커야 한다.
        # M1-a에서 429가 났다. 대기 6초는 AI Studio의 RPM 한도를 볼 수 없을 때 시작하는 값이다.
        "gemini-3.5-flash-lite": ("GEMINI_API_KEY", 0.30, 2.50, COMPLETION, 6.0),
        "claude-sonnet-5-5": ("ANTHROPIC_API_KEY", 2.00, 10.00, COMPLETION, 0.0),
    }
    key_env, input_price, output_price, output_rule, interval_seconds = by_model[model]
    return Config(
        name=name,
        api=api,
        base_url=base_url,
        model=model,
        extra_body=extra_body,
        key_env=key_env,
        input_price=input_price,
        output_price=output_price,
        output_rule=output_rule,
        interval_seconds=interval_seconds,
    )


CONFIGS = {
    config.name: config
    for config in (
        # 기준선. 운영과 같게 reasoning_effort를 보내지 않는다(기본값 max).
        _config("kimi-max", "openai_compatible", KIMI, "kimi-k3", None),
        _config("kimi-low", "openai_compatible", KIMI, "kimi-k3", {"reasoning_effort": "low"}),
        _config(
            "luna-none", "openai_compatible", OPENAI, "gpt-6-luna", {"reasoning_effort": "none"}
        ),
        _config("luna-low", "openai_compatible", OPENAI, "gpt-6-luna", {"reasoning_effort": "low"}),
        # none과 minimal이 없어 최소 수준이 low다.
        _config("sol-low", "openai_compatible", OPENAI, "gpt-6.1-sol", {"reasoning_effort": "low"}),
        _config("grok-none", "openai_compatible", XAI, "grok-4.3", {"reasoning_effort": "none"}),
        _config("grok-low", "openai_compatible", XAI, "grok-4.3", {"reasoning_effort": "low"}),
        _config(
            "gemini-minimal",
            "openai_compatible",
            GEMINI,
            "gemini-3.5-flash-lite",
            _gemini_thinking("minimal"),
        ),
        _config(
            "gemini-low",
            "openai_compatible",
            GEMINI,
            "gemini-3.5-flash-lite",
            _gemini_thinking("low"),
        ),
        _config(
            "sonnet-min",
            "anthropic",
            ANTHROPIC,
            "claude-sonnet-5-5",
            {"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}},
        ),
        _config(
            "sonnet-low",
            "anthropic",
            ANTHROPIC,
            "claude-sonnet-5-5",
            {"output_config": {"effort": "low"}},
        ),
    )
}

BASELINE = "kimi-max"
# 추천 정확도가 이 문항 수 이상이어야 고른다. 지금 golden set에서 kimi-k3가 낸 최저치다.
FLOOR = 35
# 하한에서 이만큼 안이면 경계라 한 번 더 돌린다. 측정마다 2~3문항씩 흔들린다.
MARGIN = 2
# 가장 빠른 p95와의 차이가 요청 전체 p95의 이 비율 안이면 성능이 비슷한 것으로 본다.
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
    return {
        "base_url": config.base_url,
        "model": config.model,
        "extra_body": config.extra_body,
        "api": config.api,
    }


def validate(run: Run, reference: dict) -> None:
    """측정 설정과 다르게 돌았거나 고정 입력과 다른 입력을 받은 결과면 InvalidResults를 낸다.

    reference는 고정 입력의 출처(kimi-max-r1)다. 추천 단계 prompt에는 후보 이름 말고도
    platform, push 날짜, 근거 본문이 들어가므로 문항별 prompt를 글자 그대로 비교한다.
    """
    where = f"{run.config} {run.round}회차"
    config = CONFIGS.get(run.config)
    if config is None:
        raise InvalidResults(f"{where}: 모르는 측정 설정이다")
    meta = run.document.get("meta", {})
    expected = reference["meta"]
    if meta.get("mode") != "fixed-plans":
        raise InvalidResults(f"{where}: mode가 {meta.get('mode')!r}다. 'fixed-plans'여야 한다")
    if meta.get("plans_from") != REFERENCE_NAME:
        raise InvalidResults(f"{where}: 고정 입력이 {meta.get('plans_from')!r}다")
    if meta.get("llm_provider") != "openai_compatible":
        raise InvalidResults(f"{where}: llm_provider가 {meta.get('llm_provider')!r}다")
    # fake embedder면 후보와 근거가 달라진다.
    for key in ("embedder", "embedding_model"):
        if meta.get(key) != expected.get(key):
            raise InvalidResults(f"{where}: embedder 설정({key})이 고정 입력과 다르다")
    if (meta.get("llm_stages") or {}).get("recommend") != stage_meta(config):
        raise InvalidResults(f"{where}: 추천 단계 설정이 측정 설정과 다르다")
    if meta.get("goldenset_sha256") != expected.get("goldenset_sha256"):
        raise InvalidResults(f"{where}: golden set hash가 고정 입력과 다르다")
    if run.document.get("skipped"):
        raise InvalidResults(f"{where}: 미측정 문항이 있다")
    prompts = {case["case_id"]: case.get("advise_prompt") for case in run.document["cases"]}
    reference_prompts = {case["case_id"]: case.get("advise_prompt") for case in reference["cases"]}
    if sorted(prompts) != sorted(reference_prompts):
        raise InvalidResults(f"{where}: 측정한 문항이 고정 입력의 문항과 다르다")
    changed = [
        case_id for case_id in reference_prompts if prompts[case_id] != reference_prompts[case_id]
    ]
    if changed:
        raise InvalidResults(
            f"{where}: 추천 단계 prompt가 고정 입력과 다르다: {', '.join(changed)}"
        )


@dataclass(frozen=True)
class Row:
    """측정 설정 하나의 비교 표 행. 문항 수 지표는 회차 평균이다.

    지연은 재시도가 없는 회차의 문항만 합쳐 계산한다. 그런 회차가 없으면 모든 회차로
    계산하되 latency_trusted가 False다.
    """

    config: str
    rounds: int
    # 1회차의 추천 정확도 문항 수. 경계인지 이것으로 판단한다.
    first_round: int
    accuracy: float
    tag_real: float
    sources_ok: float
    rejected: float
    p50: float
    p95: float
    # 회차마다 추천 단계 호출의 시도 횟수가 1보다 큰 문항 수.
    retried_cases: list[int]
    latency_trusted: bool
    # token과 비용은 회차 평균이라 늘 golden set 한 벌 기준이다. 모르면 None이다.
    input_tokens: float | None
    output_tokens: float | None
    reasoning_tokens: float | None
    cost: float | None
    # token 수가 있는 추천 단계 호출의 비율.
    usage_coverage: float
    # 실패한 호출 수. 오류 문장의 ":" 앞부분으로 묶는다.
    failures: dict[str, int]


@dataclass(frozen=True)
class Outcome:
    """kind는 다음 중 하나다.

    - chosen: config를 고른다.
    - incomplete: kimi-max가 없어 비교할 수 없다.
    - needs_runs: 고르기 전에 더 돌려야 한다. reasons에 무엇을 돌릴지 적는다.
    - inconclusive: 비용을 모르거나 동점이라 가를 수 없다. 사람이 정한다.
    - keep_kimi: 고를 수 있는 측정 설정이 남지 않아 kimi-k3(운영 설정)를 유지한다.
    """

    kind: str
    config: str | None
    reasons: list[str]


@dataclass(frozen=True)
class Summary:
    rows: dict[str, Row]
    outcome: Outcome
    # 참고 기준(kimi-max-r1의 추천 단계 p95 + M1-a에서 고른 조건 추출 단계 p95)으로 낸 판정.
    alt_outcome: Outcome
    request_p95: float
    alt_request_p95: float
    # 실험 전체에 쓴 비용. 모든 회차를 더한다. 하나라도 모르면 None이다.
    total_cost: float | None


def nearest_rank(values: list[float], q: float) -> float:
    """값을 작은 순으로 놓고 ⌈q·n⌉번째 값. 보간하지 않는다."""
    ordered = sorted(values)
    return ordered[max(math.ceil(q * len(ordered)), 1) - 1]


def recommend_calls(case: dict) -> list[dict]:
    return [call for call in case.get("llm_calls", []) if call["stage"] == "recommend"]


def count_retried_cases(run: Run) -> int:
    return sum(
        any(call["attempts"] > 1 for call in recommend_calls(case))
        for case in run.document["cases"]
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


def _mean_or_none(values: list) -> float | None:
    return None if None in values else mean(values)


def _count(cases: list[dict], key: str) -> int:
    return sum(bool(case.get(key)) for case in cases)


def _row(config: str, runs: list[Run]) -> Row:
    retried = [count_retried_cases(run) for run in runs]
    clean = [run for run, count in zip(runs, retried, strict=True) if count == 0]
    # 기준선에서 검색 조건 추출에 실패한 문항은 추천 단계를 부르지 않아 시간이 없다.
    seconds = [
        case["seconds_advise"]
        for run in clean or runs
        for case in run.document["cases"]
        if case.get("seconds_advise") is not None
    ]
    per_round = [run.document["cases"] for run in runs]
    usages = [
        usage_of([call for case in cases for call in recommend_calls(case)], CONFIGS[config])
        for cases in per_round
    ]
    calls = [call for cases in per_round for case in cases for call in recommend_calls(case)]
    reported = [call for call in calls if call["input_tokens"] is not None]
    failures: dict[str, int] = {}
    for call in calls:
        if not call["ok"]:
            reason = (call["error"] or "").split(":")[0]
            failures[reason] = failures.get(reason, 0) + 1
    return Row(
        config=config,
        rounds=len(runs),
        first_round=_count(per_round[0], "accurate"),
        accuracy=mean(_count(cases, "accurate") for cases in per_round),
        tag_real=mean(_count(cases, "tag_real") for cases in per_round),
        sources_ok=mean(_count(cases, "sources_ok") for cases in per_round),
        rejected=mean(_count(cases, "rejected_pick") for cases in per_round),
        p50=nearest_rank(seconds, 0.50),
        p95=nearest_rank(seconds, 0.95),
        retried_cases=retried,
        latency_trusted=bool(clean),
        input_tokens=_mean_or_none([usage.input_tokens for usage in usages]),
        output_tokens=_mean_or_none([usage.output_tokens for usage in usages]),
        reasoning_tokens=_mean_or_none([usage.reasoning_tokens for usage in usages]),
        cost=_mean_or_none([usage.cost for usage in usages]),
        usage_coverage=len(reported) / len(calls) if calls else 0.0,
        failures=failures,
    )


def summarize(
    runs: list[Run],
    reference: dict,
    snapshots: dict[int, tuple[dict, dict]],
    *,
    request_p95: float,
    alt_request_p95: float,
) -> Summary:
    """snapshots는 회차마다 측정 전과 후에 기록한 DB 상태다.

    request_p95는 kimi-max-r1의 요청 전체 p95(상수)이고, alt_request_p95는 참고 기준의 바탕이다.
    """
    for round_ in sorted({run.round for run in runs}):
        if round_ not in snapshots:
            raise InvalidResults(f"{round_}회차의 전후 snapshot이 없다")
        before, after = snapshots[round_]
        if before != after:
            raise InvalidResults(f"{round_}회차 전후 snapshot이 다르다. 그 회차 전체가 무효다")
    for run in runs:
        validate(run, reference)

    by_config: dict[str, list[Run]] = {}
    for run in sorted(runs, key=lambda r: (r.config, r.round)):
        by_config.setdefault(run.config, []).append(run)
    rows = {name: _row(name, config_runs) for name, config_runs in by_config.items()}
    costs = [
        usage_of(
            [call for case in run.document["cases"] for call in recommend_calls(case)],
            CONFIGS[run.config],
        ).cost
        for run in runs
    ]
    return Summary(
        rows=rows,
        outcome=choose(rows, request_p95),
        alt_outcome=choose(rows, alt_request_p95),
        request_p95=request_p95,
        alt_request_p95=alt_request_p95,
        total_cost=None if None in costs else sum(costs),
    )


def _near_floor(row: Row) -> bool:
    return abs(row.first_round - FLOOR) <= MARGIN


def choose(rows: dict[str, Row], request_p95: float) -> Outcome:
    """spec §집계와 고르는 기준. rows의 순서와 무관하게 같은 답을 낸다.

    두 회차 이상 잰 평균이 하한과 1문항 안이면 판정은 그대로 내되 이유에 적는다.
    흔들림 폭 안이라 회차를 더 늘릴지는 사람이 정한다.
    """
    outcome = _choose(rows, request_p95)
    if outcome.kind in ("incomplete", "needs_runs"):
        return outcome
    borderline = [
        f"{row.config}: 평균 {row.accuracy:g}문항이 하한과 1문항 안이다. "
        "회차를 더 늘릴지는 사람이 정한다"
        for row in sorted(rows.values(), key=lambda r: r.config)
        if row.rounds >= 2 and abs(row.accuracy - FLOOR) <= 1
    ]
    return replace(outcome, reasons=[*borderline, *outcome.reasons])


def _choose(rows: dict[str, Row], request_p95: float) -> Outcome:
    if BASELINE not in rows:
        return Outcome("incomplete", None, ["kimi-max가 없어 같은 시기의 기준선과 비교할 수 없다"])

    pending = [
        f"{row.config}: 1회차가 하한 ±{MARGIN}문항 안이라 한 번 더 돌린다"
        for row in sorted(rows.values(), key=lambda r: r.config)
        if _near_floor(row) and row.rounds < 2
    ]
    if pending:
        return Outcome("needs_runs", None, pending)

    pool = sorted((row for row in rows.values() if row.accuracy >= FLOOR), key=lambda r: r.config)
    reasons: list[str] = []
    similar = request_p95 * SIMILAR_LATENCY
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
            best = max(row.accuracy for row in near)
            top = [row for row in near if row.accuracy == best]
            if len(top) > 1:
                names = ", ".join(row.config for row in top)
                return Outcome("inconclusive", None, [f"{names}: 성능, 비용, 정확도가 모두 같다"])
        winner = top[0]
        if winner.latency_trusted:
            return Outcome("chosen", winner.config, reasons)
        rounds_before_confirmation = 2 if _near_floor(winner) else 1
        if winner.rounds - rounds_before_confirmation < MAX_CONFIRMATIONS:
            return Outcome(
                "needs_runs", None, [f"{winner.config}: 재시도 없는 확인 회차가 필요하다"]
            )
        reasons.append(f"{winner.config}: 확인 회차 {MAX_CONFIRMATIONS}번에도 재시도가 섞여 뺐다")
        pool = [row for row in pool if row is not winner]
    return Outcome(
        "keep_kimi", None, [*reasons, "고를 수 있는 측정 설정이 남지 않아 kimi-k3를 유지한다"]
    )


def _number(value: float | None, digits: int = 1, unknown: str = "알 수 없음") -> str:
    return unknown if value is None else f"{value:,.{digits}f}"


def _outcome_line(outcome: Outcome) -> str:
    return outcome.kind + (f" ({outcome.config})" if outcome.config else "")


def render(summary: Summary) -> str:
    """비교 표와 결론을 Markdown으로. 보조 지표가 kimi-max보다 나쁘면 ▼를 붙인다."""
    baseline = summary.rows.get(BASELINE)

    def auxiliary(row: Row, key: str, higher_is_better: bool = True) -> str:
        value = getattr(row, key)
        worse = baseline is not None and (
            value < getattr(baseline, key) if higher_is_better else value > getattr(baseline, key)
        )
        return f"{value:g}{' ▼' if worse else ''}"

    lines = [
        "| 측정 설정 | 회차 | 추천 정확도 | 태그 실재 | 출처 제공 | verify 거부 "
        "| p50(초) | p95(초) | 재시도 문항 | 실패 호출 | 입력 token | 출력 token | reasoning token "
        "| usage 보고율 | 40문항 비용(USD) |",
        "|" + " --- |" * 15,
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
            f"| {name} | {row.rounds} | {row.accuracy:g} | {auxiliary(row, 'tag_real')} "
            f"| {auxiliary(row, 'sources_ok')} | {auxiliary(row, 'rejected', False)} "
            f"| {row.p50:.1f}{latency_note} | {row.p95:.1f}{latency_note} "
            f"| {retried} | {failures or '-'} "
            f"| {_number(row.input_tokens, 0)} | {_number(row.output_tokens, 0)} "
            f"| {_number(row.reasoning_tokens, 0, unknown='-')} "
            f"| {row.usage_coverage:.0%} | {_number(row.cost, 4)} |"
        )
    lines += [
        "",
        f"정확도 하한: {FLOOR}/40. 하한 ±{MARGIN}문항 안의 1회차는 한 번 더 돌린다.",
        f"요청 전체 p95(kimi-max-r1): {summary.request_p95:.1f}초. 가장 빠른 p95에서 "
        f"{summary.request_p95 * SIMILAR_LATENCY:.1f}초 안이면 성능이 비슷한 것으로 본다.",
        f"실험 전체 비용: {_number(summary.total_cost, 4)} USD",
        "",
        f"결론: {_outcome_line(summary.outcome)}",
        *[f"- {reason}" for reason in summary.outcome.reasons],
        "",
        f"참고 기준(바탕 {summary.alt_request_p95:.1f}초, 5%는 "
        f"{summary.alt_request_p95 * SIMILAR_LATENCY:.1f}초): {_outcome_line(summary.alt_outcome)}",
        *[f"- {reason}" for reason in summary.alt_outcome.reasons],
    ]
    return "\n".join(lines)
