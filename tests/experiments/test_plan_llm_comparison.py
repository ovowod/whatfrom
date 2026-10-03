"""M1-a 실험(조건 추출 단계의 LLM 비교)의 집계 규칙.

실험 코드는 eval/experiments/ 아래 날짜 폴더에 있어 package로 import할 수 없다.
그래서 폴더를 sys.path에 넣고 module 이름으로 가져온다.
"""

import sys
from pathlib import Path

import pytest

EXPERIMENT = Path(__file__).parents[2] / "eval" / "experiments" / "2026-10-03-plan-llm-comparison"
sys.path.insert(0, str(EXPERIMENT))

from plan_llm import CONFIGS, InvalidResults, Run, stage_meta, summarize  # noqa: E402

CASES = ["q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q9", "q10"]
SAME_REPOSITORIES = ({"python": 3}, {"python": 3})


def call(
    attempts=1,
    ok=True,
    error=None,
    input_tokens=1000,
    output_tokens=100,
    reasoning=None,
    stage="plan",
):
    return {
        "stage": stage,
        "attempts": attempts,
        "ok": ok,
        "error": error,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
    }


def document(config, *, hits=10, seconds=1.0, calls=None, provider="openai_compatible", **meta):
    """hits개 문항은 두 일치율 모두 맞고, 나머지는 둘 다 틀린 결과 JSON."""
    settings = CONFIGS[config]
    stage = stage_meta(settings)
    mode = meta.pop("mode", settings.mode)
    seconds_list = seconds if isinstance(seconds, list) else [seconds] * len(CASES)
    # 요청 전체 소요 시간. 기준선의 p95가 "성능이 비슷하다"의 폭을 정한다.
    request_seconds = meta.pop("request_seconds", 60.0)
    return {
        "meta": {
            "mode": mode,
            "llm_provider": provider,
            # 추천 단계까지 도는 기준선은 실제 embedder로 돌아야 한다.
            "embedder": meta.pop("embedder", "openai_compatible" if mode == "full" else "fake"),
            "goldenset_sha256": meta.pop("sha", "golden"),
            "llm_stages": {
                "plan": meta.pop("plan_stage", stage),
                "recommend": stage if mode == "full" else None,
            },
        },
        "cases": [
            {
                "case_id": case_id,
                "repository_extracted": index < hits,
                "plan_matched": index < hits,
                "seconds_plan": seconds_list[index],
                "seconds_total": request_seconds,
                "llm_calls": calls[index] if calls is not None else [call()],
            }
            for index, case_id in enumerate(meta.pop("case_ids", CASES))
        ],
        "skipped": [],
    }


def run(config, round_=1, **kwargs):
    return Run(config, round_, document(config, **kwargs))


def test_a_result_from_the_fake_provider_stops_the_summary():
    runs = [run("kimi-max"), run("luna-none", provider="fake")]

    with pytest.raises(InvalidResults, match="luna-none"):
        summarize(runs, CASES, SAME_REPOSITORIES)


@pytest.mark.parametrize(
    ("bad_run", "reason"),
    [
        (run("luna-none", plan_stage={"base_url": "x", "model": "gpt-6-luna"}), "설정"),
        (run("luna-none", mode="full"), "mode"),
        (run("luna-none", sha="other"), "golden set"),
        (run("luna-none", case_ids=CASES[:-1] + ["other"]), "문항"),
    ],
)
def test_a_result_that_does_not_match_its_measurement_stops_the_summary(bad_run, reason):
    with pytest.raises(InvalidResults, match=reason):
        summarize([run("kimi-max"), bad_run], CASES, SAME_REPOSITORIES)


def test_a_baseline_run_with_the_fake_embedder_stops_the_summary():
    with pytest.raises(InvalidResults, match="embedder"):
        summarize([run("kimi-max", embedder="fake")], CASES, SAME_REPOSITORIES)


def test_a_skipped_case_stops_the_summary():
    bad = run("luna-none")
    bad.document["skipped"] = [{"case_id": "q11", "missing": ["node"]}]

    with pytest.raises(InvalidResults, match="미측정"):
        summarize([run("kimi-max"), bad], CASES, SAME_REPOSITORIES)


def test_a_changed_repository_list_during_measurement_stops_the_summary():
    with pytest.raises(InvalidResults, match="repository"):
        summarize([run("kimi-max")], CASES, ({"python": 3}, {"python": 4}))


def test_an_unknown_measurement_name_stops_the_summary():
    with pytest.raises(InvalidResults, match="nope"):
        summarize([Run("nope", 1, document("kimi-max"))], CASES, SAME_REPOSITORIES)


def test_each_measurement_reports_its_accuracy_and_nearest_rank_latency():
    seconds = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]

    row = summarize([run("kimi-max", hits=8, seconds=seconds)], CASES, SAME_REPOSITORIES).rows[
        "kimi-max"
    ]

    assert (row.repository, row.plan) == (8, 8)
    # 10개 중 p50은 ⌈5⌉번째, p95는 ⌈9.5⌉ = 10번째 값이다.
    assert (row.p50, row.p95) == (5.0, 10.0)


def test_accuracy_of_a_measurement_run_twice_is_the_mean_of_its_rounds():
    runs = [run("kimi-max"), run("luna-none", 1, hits=7), run("luna-none", 2, hits=8)]

    row = summarize(runs, CASES, SAME_REPOSITORIES).rows["luna-none"]

    assert (row.repository, row.plan) == (7.5, 7.5)


def retried_calls(count=1):
    """앞의 count개 문항만 재시도가 섞인 호출 기록."""
    return [[call(attempts=2)] if i < count else [call()] for i in range(len(CASES))]


def test_latency_uses_only_rounds_without_retries():
    runs = [
        run("kimi-max"),
        run("luna-none", 1, seconds=50.0, calls=retried_calls()),
        run("luna-none", 2, seconds=2.0),
    ]

    row = summarize(runs, CASES, SAME_REPOSITORIES).rows["luna-none"]

    assert row.p95 == 2.0
    assert row.retried_cases == [1, 0]
    assert row.latency_trusted


def test_latency_of_a_measurement_with_only_retried_rounds_is_not_trusted():
    runs = [run("kimi-max"), run("luna-none", seconds=3.0, calls=retried_calls(2))]

    row = summarize(runs, CASES, SAME_REPOSITORIES).rows["luna-none"]

    assert row.p95 == 3.0
    assert row.retried_cases == [2]
    assert not row.latency_trusted


def test_cost_counts_openai_completion_tokens_as_billed_output():
    row = summarize([run("kimi-max"), run("luna-none")], CASES, SAME_REPOSITORIES).rows["luna-none"]

    # 10문항 × (입력 1000, 출력 100). $0.10, $0.50 / 1M token.
    assert (row.input_tokens, row.output_tokens) == (10_000, 1_000)
    assert row.cost == pytest.approx(0.0015)
    assert row.usage_coverage == 1.0


def test_cost_adds_xai_reasoning_tokens_left_out_of_completion_tokens():
    calls = [[call(output_tokens=100, reasoning=50)] for _ in CASES]

    row = summarize(
        [run("kimi-max"), run("grok-none", calls=calls)], CASES, SAME_REPOSITORIES
    ).rows["grok-none"]

    # 출력 (100 + 50) × 10 = 1500. $1.25, $2.50 / 1M token.
    assert row.output_tokens == 1_500
    assert row.cost == pytest.approx(0.0125 + 0.00375)


def test_cost_is_unknown_while_the_gemini_token_convention_is_undecided():
    row = summarize([run("kimi-max"), run("gemini-minimal")], CASES, SAME_REPOSITORIES).rows[
        "gemini-minimal"
    ]

    assert row.cost is None


def test_a_call_without_usage_makes_the_cost_unknown_and_lowers_the_coverage():
    calls = [[call()] for _ in CASES]
    calls[0] = [call(ok=False, error="request timed out", input_tokens=None, output_tokens=None)]

    row = summarize(
        [run("kimi-max"), run("luna-none", calls=calls)], CASES, SAME_REPOSITORIES
    ).rows["luna-none"]

    assert row.cost is None
    assert row.usage_coverage == pytest.approx(0.9)
    assert row.failures == {"request timed out": 1}


def test_cost_of_a_measurement_run_twice_stays_per_golden_set_while_the_total_adds_up():
    runs = [run("kimi-max"), run("luna-none", 1), run("luna-none", 2)]

    summary = summarize(runs, CASES, SAME_REPOSITORIES)

    assert summary.rows["luna-none"].cost == pytest.approx(0.0015)
    # kimi-max: 10 × (1000 × $3 + 100 × $15) / 1M = 0.045
    assert summary.total_cost == pytest.approx(0.045 + 2 * 0.0015)


# 기준선이 10문항을 모두 맞히면 하한은 8이다. 6~10문항이 경계라 두 번 돌린다.
def twice(config, **kwargs):
    return [run(config, 1, **kwargs), run(config, 2, **kwargs)]


def test_nothing_is_chosen_without_the_baseline():
    outcome = summarize(twice("luna-none"), CASES, SAME_REPOSITORIES).outcome

    assert outcome.kind == "incomplete"
    assert outcome.config is None


def test_a_measurement_near_the_floor_is_run_again_before_choosing():
    outcome = summarize(
        [run("kimi-max"), run("luna-none", hits=7)], CASES, SAME_REPOSITORIES
    ).outcome

    assert outcome.kind == "needs_runs"
    assert any("luna-none" in reason for reason in outcome.reasons)


def test_a_measurement_below_the_floor_on_average_is_not_chosen():
    runs = [
        run("kimi-max", seconds=50.0),
        run("luna-none", 1, hits=8, seconds=1.0),
        run("luna-none", 2, hits=7, seconds=1.0),
    ]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert (outcome.kind, outcome.config) == ("chosen", "kimi-max")


def test_the_cheapest_measurement_wins_within_five_percent_of_the_baseline_request():
    # 기준선 요청 p95가 100초라 가장 빠른 p95(10초)에서 5초 안이 성능이 비슷한 묶음이다.
    runs = [
        run("kimi-max", seconds=200.0, request_seconds=100.0),
        *twice("grok-none", seconds=10.0, calls=[[call(reasoning=0)] for _ in CASES]),
        # 더 느리고 덜 정확하지만 묶음 안이고 가장 싸다.
        *twice("luna-none", hits=9, seconds=14.0),
        # 묶음에 잘못 들어가면 비용을 몰라 결론 없음이 된다.
        *twice("gemini-minimal", seconds=16.0),
    ]

    for ordered in (runs, list(reversed(runs))):
        outcome = summarize(ordered, CASES, SAME_REPOSITORIES).outcome
        assert (outcome.kind, outcome.config) == ("chosen", "luna-none")


def test_costs_within_ten_percent_are_broken_by_accuracy():
    runs = [
        run("kimi-max", seconds=50.0),
        *twice("luna-none", hits=9, seconds=1.0),
        # 비용이 3% 비싸지만 10% 안이라 비용이 같은 것으로 보고, 더 정확한 쪽을 고른다.
        *twice("luna-low", seconds=1.0, calls=[[call(output_tokens=110)] for _ in CASES]),
    ]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert (outcome.kind, outcome.config) == ("chosen", "luna-low")


def test_an_unknown_cost_in_a_tie_leaves_no_conclusion():
    runs = [
        run("kimi-max", seconds=50.0),
        *twice("luna-none", seconds=1.0),
        *twice("gemini-minimal", seconds=1.0),
    ]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert outcome.kind == "inconclusive"


def test_a_winner_with_only_retried_rounds_needs_a_confirmation_round():
    runs = [run("kimi-max", seconds=50.0), *twice("luna-none", seconds=1.0, calls=retried_calls())]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert outcome.kind == "needs_runs"
    assert any("luna-none" in reason for reason in outcome.reasons)


def test_a_winner_still_retrying_after_two_confirmations_is_dropped():
    retried = {"seconds": 1.0, "calls": retried_calls()}
    runs = [
        run("kimi-max", seconds=50.0),
        *[run("luna-none", round_, **retried) for round_ in (1, 2, 3, 4)],
    ]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert (outcome.kind, outcome.config) == ("chosen", "kimi-max")


def test_kimi_k3_stays_when_no_measurement_is_left():
    runs = [run("kimi-max", seconds=50.0, calls=retried_calls()), *twice("luna-none", hits=5)]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert outcome.kind == "keep_kimi"


def test_the_baseline_is_not_run_again():
    with pytest.raises(InvalidResults, match="기준선"):
        summarize([run("kimi-max", 1), run("kimi-max", 2)], CASES, SAME_REPOSITORIES)


def test_a_baseline_with_retries_is_dropped_without_confirmation_rounds():
    runs = [run("kimi-max", seconds=1.0, calls=retried_calls()), *twice("luna-none", seconds=9.0)]

    outcome = summarize(runs, CASES, SAME_REPOSITORIES).outcome

    assert (outcome.kind, outcome.config) == ("chosen", "luna-none")


def test_reasoning_tokens_are_reported_only_when_the_provider_reports_them():
    calls = [[call(reasoning=50)] for _ in CASES]

    rows = summarize(
        [run("kimi-max"), run("grok-none", calls=calls)], CASES, SAME_REPOSITORIES
    ).rows

    assert rows["grok-none"].reasoning_tokens == 500
    assert rows["kimi-max"].reasoning_tokens is None


def test_the_total_cost_includes_the_recommend_calls_of_the_baseline():
    calls = [[call(), call(stage="recommend")] for _ in CASES]

    summary = summarize([run("kimi-max", calls=calls)], CASES, SAME_REPOSITORIES)

    # 조건 추출 단계 비용은 0.045. 같은 크기의 추천 단계 호출이 한 번씩 더 있다.
    assert summary.rows["kimi-max"].cost == pytest.approx(0.045)
    assert summary.total_cost == pytest.approx(0.09)
