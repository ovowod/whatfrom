"""M1-b 실험(추천 단계의 LLM 비교)의 결과 검증과 판정 규칙.

실험 코드는 eval/experiments/ 아래 날짜 폴더에 있어 package로 import할 수 없다.
그래서 폴더를 sys.path에 넣고 module 이름으로 가져온다.
"""

import copy
import sys
from pathlib import Path

import pytest

EXPERIMENT = (
    Path(__file__).parents[2] / "eval" / "experiments" / "2026-10-04-recommend-llm-comparison"
)
sys.path.insert(0, str(EXPERIMENT))

from recommend_llm import (  # noqa: E402
    CONFIGS,
    REFERENCE_NAME,
    InvalidResults,
    Run,
    stage_meta,
    summarize,
    validate,
)

CASES = ["q1", "q2", "q3"]


def reference() -> dict:
    """고정 입력의 출처(kimi-max-r1)처럼 보이는 전체 평가 결과."""
    return {
        "meta": {
            "mode": "full",
            "embedder": "openai_compatible",
            "embedding_model": "bge-m3",
            "goldenset_sha256": "golden",
        },
        "cases": [{"case_id": case_id, "advise_prompt": f"prompt {case_id}"} for case_id in CASES],
        "skipped": [],
    }


def result(config: str) -> dict:
    """측정 설정대로, 고정 입력을 받아 돈 것처럼 보이는 결과."""
    return {
        "meta": {
            "mode": "fixed-plans",
            "plans_from": REFERENCE_NAME,
            "llm_provider": "openai_compatible",
            "embedder": "openai_compatible",
            "embedding_model": "bge-m3",
            "goldenset_sha256": "golden",
            "llm_stages": {"plan": None, "recommend": stage_meta(CONFIGS[config])},
        },
        "cases": [{"case_id": case_id, "advise_prompt": f"prompt {case_id}"} for case_id in CASES],
        "skipped": [],
    }


def test_a_result_measured_as_configured_on_the_fixed_input_is_valid():
    validate(Run("sonnet-low", 1, result("sonnet-low")), reference())


def test_the_baseline_may_be_measured_again():
    """지연은 공급자 부하에 따라 달라 kimi-max도 회차마다 다시 잰다."""
    validate(Run("kimi-max", 2, result("kimi-max")), reference())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda doc: doc["meta"].update(mode="full"), "mode"),
        (lambda doc: doc["meta"].update(plans_from="other.json"), "고정 입력"),
        (lambda doc: doc["meta"].update(llm_provider="fake"), "llm_provider"),
        (lambda doc: doc["meta"].update(embedder="fake"), "embedder"),
        (lambda doc: doc["meta"].update(embedding_model="other"), "embedder"),
        (lambda doc: doc["meta"]["llm_stages"]["recommend"].update(model="x"), "추천 단계 설정"),
        (lambda doc: doc["meta"]["llm_stages"]["recommend"].update(api="x"), "추천 단계 설정"),
        (lambda doc: doc["meta"].update(goldenset_sha256="other"), "golden set"),
        (lambda doc: doc.update(skipped=[{"case_id": "q4"}]), "미측정"),
        (lambda doc: doc["cases"].pop(), "문항"),
    ],
)
def test_a_result_that_differs_from_its_configuration_is_invalid(change, message):
    document = result("sonnet-low")
    change(document)

    with pytest.raises(InvalidResults, match=message):
        validate(Run("sonnet-low", 1, document), reference())


def test_a_changed_prompt_is_invalid_and_names_the_cases():
    """후보 이름이 같아도 근거 본문이나 push 날짜가 바뀌면 다른 입력이다."""
    document = result("luna-low")
    document["cases"][0]["advise_prompt"] = "prompt q1 with newer evidence"
    document["cases"][2]["advise_prompt"] = "prompt q3 with newer evidence"

    with pytest.raises(InvalidResults, match="q1, q3"):
        validate(Run("luna-low", 1, document), reference())


def test_an_unknown_configuration_is_invalid():
    with pytest.raises(InvalidResults, match="모르는"):
        validate(Run("nobody", 1, copy.deepcopy(result("luna-low"))), reference())


# 판정 규칙은 golden set 40문항 기준이다(정확도 하한 35/40).
ALL_CASES = [f"c{i:02d}" for i in range(40)]
SNAPSHOT = {"repositories": {"python": 3}, "digests": {"python:3.13-slim": "sha256:a"}}
# 기준선 요청 전체 p95(상수). 5%는 2초다.
REQUEST_P95 = 40.0


def full_reference() -> dict:
    document = reference()
    document["cases"] = [{"case_id": c, "advise_prompt": f"prompt {c}"} for c in ALL_CASES]
    return document


def call(attempts=1, input_tokens=2000, output_tokens=1000, reasoning=None, ok=True):
    return {
        "stage": "recommend",
        "attempts": attempts,
        "ok": ok,
        "error": None if ok else "timeout: read",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
    }


def measured(config: str, round_: int, hits: int, seconds: float, retried: int = 0, **usage) -> Run:
    """앞에서 hits개 문항이 정답이고, 추천 단계가 모두 seconds초 걸린 회차."""
    document = result(config)
    document["cases"] = [
        {
            "case_id": case_id,
            "advise_prompt": f"prompt {case_id}",
            "accurate": index < hits,
            "tag_real": True,
            "sources_ok": True,
            "rejected_pick": False,
            "seconds_advise": seconds,
            "llm_calls": [call(attempts=2 if index < retried else 1, **usage)],
        }
        for index, case_id in enumerate(ALL_CASES)
    ]
    return Run(config, round_, document)


def summary_of(runs: list[Run], snapshots=None, alt_request_p95: float = REQUEST_P95):
    rounds = {run.round for run in runs}
    return summarize(
        runs,
        full_reference(),
        snapshots if snapshots is not None else {r: (SNAPSHOT, SNAPSHOT) for r in rounds},
        request_p95=REQUEST_P95,
        alt_request_p95=alt_request_p95,
    )


def test_the_fastest_setting_above_the_floor_is_chosen():
    """luna-low는 하한 아래라 빠져도 고르지 않는다. 비용이 열 배 넘게 차이 나도 성능이 먼저다."""
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            measured("luna-low", 1, 30, 3.0),
            measured("sol-low", 1, 39, 8.0),
            measured("luna-none", 1, 39, 12.0),
        ]
    )

    assert summary.outcome.kind == "chosen"
    assert summary.outcome.config == "sol-low"


def test_the_outcome_does_not_depend_on_the_order_of_the_results():
    runs = [
        measured("kimi-max", 1, 39, 45.0),
        measured("sol-low", 1, 39, 8.0),
        measured("luna-none", 1, 39, 9.0),
    ]

    assert summary_of(runs).outcome == summary_of(list(reversed(runs))).outcome


def test_among_similar_latencies_the_cheapest_is_chosen():
    """8초와 9초는 기준선 요청 전체 p95의 5%(2초) 안이라 비용으로 가른다."""
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            measured("sol-low", 1, 39, 8.0),
            measured("luna-none", 1, 39, 9.0),
        ]
    )

    assert summary.outcome.config == "luna-none"
    # sol-low: 40 × (2000 × $2 + 1000 × $10) / 1M = $0.56
    # luna-none: 40 × (2000 × $0.1 + 1000 × $0.5) / 1M = $0.028
    assert summary.rows["sol-low"].cost == pytest.approx(0.56)
    assert summary.rows["luna-none"].cost == pytest.approx(0.028)


def test_a_first_round_near_the_floor_needs_another_round():
    """33~37문항은 측정마다 흔들리는 폭 안이라 한 번으로 판정하지 않는다."""
    summary = summary_of([measured("kimi-max", 1, 39, 45.0), measured("grok-none", 1, 36, 5.0)])

    assert summary.outcome.kind == "needs_runs"
    assert "grok-none" in summary.outcome.reasons[0]


def test_accuracy_is_the_mean_of_all_rounds():
    """36과 33의 평균 34.5는 하한 35 아래다."""
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            measured("kimi-max", 2, 39, 45.0),
            measured("grok-none", 1, 36, 5.0),
            measured("grok-none", 2, 33, 5.0),
        ]
    )

    assert summary.rows["grok-none"].accuracy == 34.5
    assert summary.outcome.config == "kimi-max"


def test_latency_comes_only_from_rounds_without_retries():
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            measured("sol-low", 1, 39, 30.0, retried=3),
            measured("sol-low", 2, 39, 8.0),
        ]
    )

    row = summary.rows["sol-low"]
    assert row.retried_cases == [3, 0]
    assert (row.p50, row.p95) == (8.0, 8.0)
    assert row.latency_trusted
    assert summary.outcome.config == "sol-low"


def test_a_winner_with_retries_needs_a_confirmation_round():
    summary = summary_of(
        [measured("kimi-max", 1, 39, 45.0), measured("sol-low", 1, 39, 8.0, retried=1)]
    )

    assert summary.outcome.kind == "needs_runs"
    assert "sol-low" in summary.outcome.reasons[0]


def test_a_setting_still_retrying_after_two_confirmations_is_left_out_of_latency():
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            *[measured("gemini-low", r, 39, 4.0, retried=2) for r in (1, 2, 3)],
            measured("sol-low", 1, 39, 8.0),
        ]
    )

    assert summary.outcome.config == "sol-low"
    assert any("gemini-low" in reason for reason in summary.outcome.reasons)


def test_an_unknown_cost_among_similar_latencies_is_inconclusive():
    """Gemini는 usage 형식을 몰라 비용을 알 수 없다. 추정치로 고르지 않는다."""
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            measured("gemini-minimal", 1, 39, 8.0),
            measured("luna-none", 1, 39, 9.0),
        ]
    )

    assert summary.rows["gemini-minimal"].cost is None
    assert summary.outcome.kind == "inconclusive"


def test_without_the_baseline_the_outcome_is_incomplete():
    assert summary_of([measured("sol-low", 1, 39, 8.0)]).outcome.kind == "incomplete"


def test_when_nothing_passes_the_floor_kimi_k3_is_kept():
    summary = summary_of([measured("kimi-max", 1, 30, 45.0), measured("sol-low", 1, 30, 8.0)])

    assert summary.outcome.kind == "keep_kimi"


def test_the_reference_judgment_uses_its_own_latency_base():
    """참고 기준은 바뀔 운영 상태(추천 단계 + 새 조건 추출 단계)에 가까운 바탕으로 판정한다.
    바탕이 20초면 5%는 1초라 8초와 9.5초가 비슷하지 않다."""
    summary = summary_of(
        [
            measured("kimi-max", 1, 39, 45.0),
            measured("sol-low", 1, 39, 8.0),
            measured("luna-none", 1, 39, 9.5),
        ],
        alt_request_p95=20.0,
    )

    assert summary.outcome.config == "luna-none"
    assert summary.alt_outcome.config == "sol-low"


@pytest.mark.parametrize(
    "snapshots",
    [
        {1: (SNAPSHOT, {**SNAPSHOT, "digests": {"python:3.13-slim": "sha256:b"}})},
        {},
    ],
)
def test_a_round_without_matching_snapshots_is_invalid(snapshots):
    with pytest.raises(InvalidResults, match="snapshot"):
        summary_of([measured("kimi-max", 1, 39, 45.0)], snapshots=snapshots)
