# tests/eval/test_citations.py
"""인용 뒷받침 표본의 추출과 채점 집계. 실제 결과 파일로 도는지는 test_cli_eval.py가 본다."""

import pytest
import yaml

from whatfrom.eval.citations import (
    dump_sample,
    render_citation_score,
    sample_citations,
    score_citations,
)


def _case(case_id: str, verified: list[bool], image: str = "python:3.13-slim") -> dict:
    citations = [
        {"evidence": 1, "quote": f"{case_id} quote {i}", "verified": v, "problem": None}
        for i, v in enumerate(verified)
    ]
    return {
        "case_id": case_id,
        "recommendation": {
            "image": image,
            "alternatives": [],
            "claims": [{"text": f"{case_id} 주장", "citations": citations}],
            "verified_citations": sum(verified),
        },
        "evidence": [
            {
                "number": 1,
                "repository": "python",
                "section_title": "Image Variants",
                "source_url": "https://example.invalid/python",
            }
        ],
    }


def _result(*cases: dict) -> dict:
    return {"meta": {"mode": "label-plans"}, "cases": list(cases)}


def _fill(items: list[dict], verdicts: list[str]) -> str:
    for item, verdict in zip(items, verdicts, strict=True):
        item["verdict"] = verdict
    return dump_sample(items)


def test_failed_citations_are_not_sampled() -> None:
    items, _ = sample_citations([("a.json", _result(_case("c1", [True, False])))], seed=0)

    assert [item["quote"] for item in items] == ["c1 quote 0"]


def test_at_most_two_pairs_per_case_and_fifty_per_arm() -> None:
    cases = [_case(f"c{i}", [True] * 5) for i in range(40)]

    items, key = sample_citations([("a.json", _result(*cases))], seed=0)

    assert len(items) == 50
    per_case: dict[str, int] = {}
    for entry in key["items"].values():
        per_case[entry["case_id"]] = per_case.get(entry["case_id"], 0) + 1
    assert max(per_case.values()) == 2


def test_the_same_seed_draws_the_same_sample() -> None:
    results = [("a.json", _result(*[_case(f"c{i}", [True] * 3) for i in range(40)]))]

    assert sample_citations(results, seed=7) == sample_citations(results, seed=7)
    assert sample_citations(results, seed=7)[0] != sample_citations(results, seed=8)[0]


def test_sample_hides_which_result_file_each_pair_came_from() -> None:
    results = [
        ("kimi-max.json", _result(_case("c1", [True]))),
        ("luna-none.json", _result(_case("c1", [True]))),
    ]

    items, key = sample_citations(results, seed=0)
    text = dump_sample(items)

    assert "kimi" not in text and "luna" not in text
    assert {entry["arm"] for entry in key["items"].values()} == {"kimi-max.json", "luna-none.json"}


def test_sample_items_show_the_statement_image_quote_and_evidence() -> None:
    items, _ = sample_citations([("a.json", _result(_case("c1", [True])))], seed=0)

    [item] = yaml.safe_load(dump_sample(items))
    assert item["statement"] == "c1 주장"
    assert item["image"] == "python:3.13-slim"
    assert item["quote"] == "c1 quote 0"
    assert item["evidence"] == "python — Image Variants"
    assert item["verdict"] is None


def _sample(count: int) -> tuple[list[dict], dict]:
    cases = [_case(f"c{i}", [True]) for i in range(count)]
    return sample_citations([("a.json", _result(*cases))], seed=0)


def test_forty_five_of_fifty_supported_passes() -> None:
    items, key = _sample(50)

    [arm] = score_citations(_fill(items, ["supported"] * 45 + ["unsupported"] * 5), key).arms

    assert (arm.supported, arm.scorable, arm.outcome) == (45, 50, "합격")


def test_forty_four_of_fifty_supported_fails() -> None:
    items, key = _sample(50)

    [arm] = score_citations(_fill(items, ["supported"] * 44 + ["unsupported"] * 6), key).arms

    assert arm.outcome == "불합격"


def test_unclear_is_left_out_of_the_denominator() -> None:
    items, key = _sample(50)

    [arm] = score_citations(
        _fill(items, ["supported"] * 44 + ["unsupported"] * 3 + ["unclear"] * 3), key
    ).arms

    assert (arm.scorable, arm.unclear, arm.outcome) == (47, 3, "합격")


def test_too_many_unclear_withholds_the_outcome() -> None:
    items, key = _sample(50)

    [arm] = score_citations(_fill(items, ["supported"] * 44 + ["unclear"] * 6), key).arms

    assert arm.outcome == "보류"


def test_no_scorable_pair_is_unmeasured() -> None:
    items, key = _sample(3)

    [arm] = score_citations(_fill(items, ["unclear"] * 3), key).arms

    assert (arm.ratio, arm.outcome) == (None, "미측정")


def test_a_short_sample_is_flagged() -> None:
    items, key = _sample(3)

    [arm] = score_citations(_fill(items, ["supported"] * 3), key).arms

    assert (arm.sampled, arm.short) == (3, True)


def test_blank_or_unknown_verdicts_withhold_every_outcome() -> None:
    """YAML은 따옴표 없는 yes를 bool로 읽는다. 세 어휘 밖의 값은 모두 잘못된 값이다."""
    items, key = _sample(3)
    text = _fill(items, ["supported", "supported", "supported"])
    text = text.replace("verdict: supported", "verdict: yes", 1)
    text = text.replace("verdict: supported", "verdict:", 1)

    score = score_citations(text, key)

    assert score.problems
    assert [arm.outcome for arm in score.arms] == [None]
    assert score.arms[0].blank == 1


def test_a_deleted_row_withholds_the_outcome() -> None:
    items, key = _sample(3)
    text = _fill(items, ["supported"] * 3)

    score = score_citations(dump_sample(yaml.safe_load(text)[:2]), key)

    assert any("빠진" in problem for problem in score.problems)
    assert score.arms[0].outcome is None


def test_a_duplicated_row_withholds_the_outcome() -> None:
    items, key = _sample(3)
    filled = yaml.safe_load(_fill(items, ["supported"] * 3))

    score = score_citations(dump_sample([*filled, filled[0]]), key)

    assert any("중복" in problem for problem in score.problems)


def test_an_edited_quote_withholds_the_outcome() -> None:
    items, key = _sample(3)
    filled = yaml.safe_load(_fill(items, ["supported"] * 3))
    filled[0]["quote"] = "다르게 고친 문장"

    score = score_citations(dump_sample(filled), key)

    assert any(filled[0]["id"] in problem for problem in score.problems)


def test_a_row_whose_id_is_not_in_the_key_withholds_the_outcome() -> None:
    items, key = _sample(3)
    filled = yaml.safe_load(_fill(items, ["supported"] * 3))
    filled[0]["id"] = "c999"

    score = score_citations(dump_sample(filled), key)

    assert any("c999" in problem for problem in score.problems)
    assert score.arms[0].outcome is None


def test_a_key_entry_for_an_unknown_arm_is_a_problem_not_a_crash() -> None:
    items, key = _sample(1)
    key["items"]["c001"]["arm"] = "other.json"

    score = score_citations(_fill(items, ["supported"]), key)

    assert any("other.json" in problem for problem in score.problems)


def test_two_results_with_the_same_name_stop_the_sampling() -> None:
    """안 이름이 겹치면 두 안의 집계가 조용히 합쳐진다."""
    result = _result(_case("c1", [True]))

    with pytest.raises(ValueError, match="a.json"):
        sample_citations([("a.json", result), ("a.json", result)], seed=0)


def test_a_verified_citation_without_its_evidence_stops_the_sampling() -> None:
    """검증을 통과한 인용은 근거가 있어야 한다. 없으면 결과 파일이 깨진 것이다."""
    case = _case("c1", [True])
    case["evidence"] = []

    with pytest.raises(ValueError, match="c1"):
        sample_citations([("a.json", _result(case))], seed=0)


def test_the_report_shows_each_arm_even_while_scoring_is_unfinished() -> None:
    """채점 도중에도 안마다 어디까지 채웠는지 볼 수 있어야 한다."""
    items, key = _sample(3)
    text = _fill(items, ["supported", "unclear", "supported"])
    text = text.replace("verdict: unclear", "verdict:", 1)

    report = render_citation_score(score_citations(text, key))

    assert "a.json" in report
    assert "빈 칸 1" in report
    assert "판정 안 함" in report


def test_two_pairs_from_one_case_come_from_different_claims_when_possible() -> None:
    """같은 주장의 인용 두 개보다 서로 다른 주장 두 개가 문항을 더 넓게 본다."""
    case = _case("c1", [True, True, True])
    other = {
        "text": "c1 다른 주장",
        "citations": [
            {"evidence": 1, "quote": "c1 other quote", "verified": True, "problem": None}
        ],
    }
    case["recommendation"]["claims"].append(other)

    for seed in range(20):
        items, _ = sample_citations([("a.json", _result(case))], seed=seed)
        assert {item["statement"] for item in items} == {"c1 주장", "c1 다른 주장"}


def test_a_case_with_one_claim_still_gives_two_pairs() -> None:
    items, _ = sample_citations([("a.json", _result(_case("c1", [True, True, True])))], seed=0)

    assert len(items) == 2
