# tests/eval/test_citations.py
"""인용 검토 표본의 추출과 집계. 실제 결과 파일로 도는지는 test_cli_eval.py가 본다."""

import pytest
import yaml

from whatfrom.eval.citations import (
    dump_sample,
    render_citation_score,
    sample_citations,
    score_citations,
)

PROMPT = (
    "Requirement: {case_id} 질문\n\n"
    "Candidates (choose exactly one):\n"
    "- python:3.13-slim | 40-45 MB across linux/amd64, linux/arm64 | pushed: 2026-09-09\n"
    "- python:3.13 | 400-420 MB across linux/amd64 | pushed: 2026-09-09\n\n"
    "Evidence from the official README:\n[1] python — Image Variants\n본문\n"
)


def _case(case_id: str, *claims: list[bool], image: str = "python:3.13-slim") -> dict:
    """claims는 주장마다 인용들의 검증 결과다. 주장 하나에 인용 하나가 기본이다."""
    return {
        "case_id": case_id,
        "advise_prompt": PROMPT.format(case_id=case_id),
        "recommendation": {
            "image": image,
            "alternatives": [],
            "claims": [
                {
                    "text": f"{case_id} 주장 {c}",
                    "citations": [
                        {"evidence": 1, "quote": f"{case_id} quote {c}.{i}", "verified": v}
                        for i, v in enumerate(verified)
                    ],
                }
                for c, verified in enumerate(claims or [[True]])
            ],
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


def _fill(items: list[dict], verdicts: list[str], check: str = "not_applicable") -> str:
    for item, verdict in zip(items, verdicts, strict=True):
        item["verdict"] = verdict
        item["candidate_ok"] = check
    return dump_sample(items)


def _sample(count: int) -> tuple[list[dict], dict]:
    return sample_citations([("a.json", _result(*[_case(f"c{i}") for i in range(count)]))], seed=0)


def test_one_item_holds_every_verified_citation_of_a_claim() -> None:
    """인용 두 개를 합쳐야 성립하는 주장이 있어, 한 주장의 인용은 함께 본다."""
    items, _ = sample_citations([("a.json", _result(_case("c1", [True, False, True])))], seed=0)

    [item] = items
    assert [c["quote"] for c in item["citations"]] == ["c1 quote 0.0", "c1 quote 0.2"]


def test_a_claim_without_a_verified_citation_is_not_sampled() -> None:
    items, _ = sample_citations([("a.json", _result(_case("c1", [False], [True])))], seed=0)

    assert [item["statement"] for item in items] == ["c1 주장 1"]


def test_sample_items_show_the_question_and_the_candidates_the_llm_saw() -> None:
    """후보 정보는 지금 DB가 아니라 그 실행에서 LLM에게 넘긴 값과 대조한다."""
    items, _ = sample_citations([("a.json", _result(_case("c1")))], seed=0)

    [item] = yaml.safe_load(dump_sample(items))
    assert item["question"] == "c1 질문"
    assert item["image"] == "python:3.13-slim"
    assert item["statement"] == "c1 주장 0"
    assert item["citations"] == [{"quote": "c1 quote 0.0", "evidence": "python — Image Variants"}]
    assert item["candidates"][0].startswith("python:3.13-slim | 40-45 MB")
    assert (item["verdict"], item["candidate_ok"]) == (None, None)


def test_the_sample_file_starts_with_the_review_rules() -> None:
    items, _ = _sample(1)

    text = dump_sample(items)

    assert text.startswith("# 인용 검토 표본")
    assert "candidate_ok" in text.split("- id:")[0]


def test_at_most_two_claims_per_case_and_fifty_per_arm() -> None:
    cases = [_case(f"c{i}", [True], [True], [True]) for i in range(40)]

    items, key = sample_citations([("a.json", _result(*cases))], seed=0)

    assert len(items) == 50
    per_case: dict[str, int] = {}
    for entry in key["items"].values():
        per_case[entry["case_id"]] = per_case.get(entry["case_id"], 0) + 1
    assert max(per_case.values()) == 2


def test_the_same_seed_draws_the_same_sample() -> None:
    results = [("a.json", _result(*[_case(f"c{i}", [True], [True], [True]) for i in range(40)]))]

    assert sample_citations(results, seed=7) == sample_citations(results, seed=7)
    assert sample_citations(results, seed=7)[0] != sample_citations(results, seed=8)[0]


def test_sample_hides_which_result_file_each_claim_came_from() -> None:
    results = [("kimi-max.json", _result(_case("c1"))), ("luna-none.json", _result(_case("c1")))]

    items, key = sample_citations(results, seed=0)
    text = dump_sample(items)

    assert "kimi" not in text and "luna" not in text
    assert {entry["arm"] for entry in key["items"].values()} == {"kimi-max.json", "luna-none.json"}


def test_a_result_without_the_prompt_stops_the_sampling() -> None:
    case = _case("c1")
    del case["advise_prompt"]

    with pytest.raises(ValueError, match="c1"):
        sample_citations([("a.json", _result(case))], seed=0)


def test_two_results_with_the_same_name_stop_the_sampling() -> None:
    """안 이름이 겹치면 두 안의 집계가 조용히 합쳐진다."""
    result = _result(_case("c1"))

    with pytest.raises(ValueError, match="a.json"):
        sample_citations([("a.json", result), ("a.json", result)], seed=0)


def test_a_verified_citation_without_its_evidence_stops_the_sampling() -> None:
    """검증을 통과한 인용은 근거가 있어야 한다. 없으면 결과 파일이 깨진 것이다."""
    case = _case("c1")
    case["evidence"] = []

    with pytest.raises(ValueError, match="c1"):
        sample_citations([("a.json", _result(case))], seed=0)


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


def test_no_scorable_claim_is_unmeasured() -> None:
    items, key = _sample(3)

    [arm] = score_citations(_fill(items, ["unclear"] * 3), key).arms

    assert (arm.ratio, arm.outcome) == (None, "미측정")


def test_a_short_sample_is_flagged() -> None:
    items, key = _sample(3)

    [arm] = score_citations(_fill(items, ["supported"] * 3), key).arms

    assert (arm.sampled, arm.short) == (3, True)


def test_candidate_checks_are_counted_apart_from_the_support_rate() -> None:
    """후보 정보가 틀려도 인용 뒷받침률은 그대로다. 둘은 서로 다른 질문이다."""
    items, key = _sample(4)
    for item, check in zip(items, ["match", "mismatch", "unclear", "not_applicable"], strict=True):
        item["verdict"] = "supported"
        item["candidate_ok"] = check

    [arm] = score_citations(dump_sample(items), key).arms

    assert (arm.ratio, arm.candidate_mismatch, arm.candidate_checked) == (1.0, 1, 2)
    assert (arm.candidate_unclear, arm.candidate_not_applicable) == (1, 1)


def test_a_blank_candidate_check_is_unfinished_not_not_applicable() -> None:
    """해당 없음까지 빈 칸으로 두면 검토를 빠뜨린 것과 구분되지 않는다."""
    items, key = _sample(2)
    _fill(items, ["supported"] * 2)
    items[0]["candidate_ok"] = None

    score = score_citations(dump_sample(items), key)

    assert score.arms[0].blank == 1
    assert score.arms[0].outcome is None


@pytest.mark.parametrize(
    ("field", "value"), [("verdict", "verdict: yes"), ("candidate_ok", "candidate_ok: no")]
)
def test_unknown_values_withhold_every_outcome(field: str, value: str) -> None:
    """YAML은 따옴표 없는 yes와 no를 bool로 읽는다. 정한 어휘 밖의 값은 모두 잘못된 값이다."""
    items, key = _sample(2)
    guide, body = _fill(items, ["supported"] * 2, check="match").split("- id:", 1)
    body = body.replace(f"{field}: {'supported' if field == 'verdict' else 'match'}", value, 1)
    text = guide + "- id:" + body

    score = score_citations(text, key)

    assert any(field in problem for problem in score.problems)
    assert score.arms[0].outcome is None


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


@pytest.mark.parametrize("edit", ["citations", "candidates"])
def test_an_edited_item_withholds_the_outcome(edit: str) -> None:
    items, key = _sample(3)
    filled = yaml.safe_load(_fill(items, ["supported"] * 3))
    filled[0][edit] = []

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


def test_the_report_shows_each_arm_even_while_review_is_unfinished() -> None:
    """검토 도중에도 안마다 어디까지 채웠는지 볼 수 있어야 한다."""
    items, key = _sample(3)
    _fill(items, ["supported", "unclear", "supported"], check="mismatch")
    items[1]["verdict"] = None

    report = render_citation_score(score_citations(dump_sample(items), key))

    assert "a.json" in report
    assert "빈 칸 1" in report
    assert "판정 안 함" in report
    assert "불일치 3/3" in report


def test_a_claim_without_document_facts_is_left_out_of_the_rate_and_the_unclear_limit() -> None:
    """ "근거만으로는 알 수 없다"만 말하는 주장은 인용으로 확인할 문서 사실이 없다."""
    items, key = _sample(50)

    [arm] = score_citations(
        _fill(items, ["supported"] * 40 + ["unsupported"] * 2 + ["no_doc_fact"] * 8), key
    ).arms

    assert (arm.scorable, arm.no_doc_fact, arm.outcome) == (42, 8, "합격")
    assert "문서 사실 없음 8" in render_citation_score(
        score_citations(_fill(items, ["supported"] * 42 + ["no_doc_fact"] * 8), key)
    )
