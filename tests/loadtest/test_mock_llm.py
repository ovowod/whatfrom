"""부하 시험용 모의 LLM 서버.

프롬프트는 앱의 실제 생성 함수로 만든다. 앱의 프롬프트 형식이 바뀌면 여기서 잡힌다.
"""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from whatfrom.core.contracts import Candidate, Evidence, Recommendation, SearchPlan
from whatfrom.loadtest.mock_llm import Recorded, create_mock_app, load_records
from whatfrom.recommend.advisor import build_prompt
from whatfrom.recommend.planner import build_plan_prompt

QUESTION = "python:3.14 랑 python:3.14-slim 이 정확히 뭐가 다른 거야?\n"
NOW = datetime(2026, 9, 24, tzinfo=UTC)


def candidate(image: str) -> Candidate:
    repository, tag = image.split(":")
    return Candidate(
        image=image,
        repository=repository,
        tag=tag,
        source_url=f"https://hub.docker.com/_/{repository}",
        collected_at=NOW,
        # 근거 본문의 "- x | y" 줄을 후보로 읽으면 안 된다.
        evidence=[
            Evidence(
                repository=repository,
                section_title="Image Variants",
                content="- evil:1 | not a candidate",
                source_url="https://github.com/docker-library/docs",
            )
        ],
    )


CANDIDATES = [candidate("python:3.14"), candidate("python:3.14-slim")]
RECORDS = {
    QUESTION.strip(): Recorded(
        plan={"repository": "python", "version_prefix": "3.14"},
        image="python:3.14-slim",
        seconds_plan=10.0,
        seconds_advise=50.0,
    ),
    "other": Recorded(plan=None, image=None, seconds_plan=30.0, seconds_advise=70.0),
}


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def chat(client: TestClient, name: str, prompt: str):
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "kimi-k3",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": name}},
        },
    )


def content(response) -> str:
    return response.json()["choices"][0]["message"]["content"]


def test_search_plan_returns_the_recorded_plan_after_the_scaled_delay():
    sleep = FakeSleep()
    client = TestClient(create_mock_app(RECORDS, scale=0.1, sleep=sleep))

    response = chat(client, "search_plan", build_plan_prompt(QUESTION, ["node", "python"]))

    assert response.status_code == 200
    assert SearchPlan.model_validate_json(content(response)) == SearchPlan(
        repository="python", version_prefix="3.14"
    )
    assert sleep.calls == [1.0]


def test_recommendation_returns_the_recorded_image_when_it_is_a_candidate():
    sleep = FakeSleep()
    client = TestClient(create_mock_app(RECORDS, sleep=sleep))

    response = chat(client, "recommendation", build_prompt(QUESTION, CANDIDATES))

    recommendation = Recommendation.model_validate_json(content(response))
    assert recommendation.image == "python:3.14-slim"
    assert recommendation.dockerfile == "FROM python:3.14-slim\n"
    assert sleep.calls == [50.0]


def test_recommendation_falls_back_to_the_first_candidate():
    """기록된 이미지가 이번 후보에 없으면 첫 후보를 준다. 검증을 통과해야 측정이 오염되지 않는다."""
    client = TestClient(create_mock_app(RECORDS, sleep=FakeSleep()))

    response = chat(client, "recommendation", build_prompt(QUESTION, [candidate("python:3.13")]))

    assert Recommendation.model_validate_json(content(response)).image == "python:3.13"


def test_an_unknown_question_gets_an_empty_plan_after_the_median_delay():
    sleep = FakeSleep()
    client = TestClient(create_mock_app(RECORDS, sleep=sleep))

    response = chat(client, "search_plan", build_plan_prompt("모르는 질문", ["python"]))

    assert SearchPlan.model_validate_json(content(response)) == SearchPlan()
    assert sleep.calls == [20.0]  # 10과 30의 중앙값


def test_an_unknown_schema_name_is_rejected():
    client = TestClient(create_mock_app(RECORDS, sleep=FakeSleep()))

    assert chat(client, "summary", "Question: x").status_code == 400


def write_inputs(tmp_path, cases: list[dict]):
    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps({"seed": 0, "questions": [{"id": "slim", "question": QUESTION}], "order": [0]})
    )
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"cases": cases}))
    return questions, results


SLIM = {
    "case_id": "slim",
    "plan": {"repository": "python"},
    "recommended_image": None,
    "seconds_plan": 1.5,
    "seconds_advise": 120.1,
}


def test_load_records_keys_by_question_and_reads_the_recorded_values(tmp_path):
    records = load_records(*write_inputs(tmp_path, [SLIM]))

    assert records == {
        QUESTION.strip(): Recorded(
            plan={"repository": "python"}, image=None, seconds_plan=1.5, seconds_advise=120.1
        )
    }


def test_load_records_rejects_a_question_without_a_record(tmp_path):
    """잘못된 결과 파일을 넣고도 부하 시험이 정상처럼 끝나면 안 된다."""
    with pytest.raises(ValueError, match="slim"):
        load_records(*write_inputs(tmp_path, [{**SLIM, "case_id": "other"}]))


@pytest.mark.parametrize("seconds", [None, -1.0])
def test_load_records_rejects_a_missing_or_negative_time(tmp_path, seconds):
    with pytest.raises(ValueError, match="slim"):
        load_records(*write_inputs(tmp_path, [{**SLIM, "seconds_advise": seconds}]))


def test_load_records_rejects_a_plan_that_is_not_a_search_plan(tmp_path):
    with pytest.raises(ValueError, match="slim"):
        load_records(*write_inputs(tmp_path, [{**SLIM, "plan": {"bogus": 1}}]))


@pytest.mark.parametrize("scale", [0.0, -1.0, float("inf"), float("nan")])
def test_a_scale_that_is_not_a_finite_positive_number_is_rejected(scale):
    with pytest.raises(ValueError, match="scale"):
        create_mock_app(RECORDS, scale=scale)
