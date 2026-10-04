"""M1-b 실험의 측정 실행, 회차 전후 snapshot, 사전 확인.

실제 공급자 API는 부르지 않는다. 측정 실행은 평가를 대신하는 함수로, 사전 확인은
가짜 transport로 확인한다.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from sqlalchemy.orm import Session

from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository

EXPERIMENT = (
    Path(__file__).parents[2] / "eval" / "experiments" / "2026-10-04-recommend-llm-comparison"
)
sys.path.insert(0, str(EXPERIMENT))

from recommend_experiment import measure, precheck, snapshot  # noqa: E402
from recommend_llm import CONFIGS, REFERENCE_NAME, stage_meta  # noqa: E402

NOW = datetime(2026, 10, 4, tzinfo=UTC)
CASES = ["q1", "q2"]
KEYS = {"OPENAI_API_KEY": "openai-key", "ANTHROPIC_API_KEY": "anthropic-key"}


def reference_file(tmp_path: Path) -> Path:
    path = tmp_path / REFERENCE_NAME
    path.write_text(
        json.dumps(
            {
                "meta": {
                    "mode": "full",
                    "embedder": "openai_compatible",
                    "embedding_model": "bge-m3",
                    "goldenset_sha256": "golden",
                },
                "cases": [{"case_id": c, "advise_prompt": f"prompt {c}"} for c in CASES],
                "skipped": [],
            }
        )
    )
    return path


def result(config: str, prompt_suffix: str = "") -> dict:
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
        "cases": [{"case_id": c, "advise_prompt": f"prompt {c}{prompt_suffix}"} for c in CASES],
        "skipped": [],
    }


class FakeEval:
    """평가 대신 결과 JSON을 쓰고, 받은 명령과 환경 변수를 남긴다."""

    def __init__(self, document: dict) -> None:
        self.document = document
        self.command: list[str] = []
        self.env: dict[str, str] = {}

    def __call__(self, command: list[str], env: dict[str, str], results_dir: Path) -> None:
        self.command = command
        self.env = env
        (results_dir / "2026-10-04T00-00-00.json").write_text(json.dumps(self.document))


def run_measure(
    config: str, tmp_path: Path, fake: FakeEval, environ: dict | None = None, **options
) -> Path:
    return measure(
        config,
        1,
        environ=KEYS if environ is None else environ,
        reference=reference_file(tmp_path),
        out_dir=tmp_path / "results",
        run_eval=fake,
        **options,
    )


def option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def test_a_measurement_runs_only_the_recommend_stage_on_the_fixed_input(tmp_path):
    fake = FakeEval(result("sonnet-low"))

    copied = run_measure("sonnet-low", tmp_path, fake)

    assert option(fake.command, "--plans") == str(tmp_path / REFERENCE_NAME)
    # 둘 다 기본값이 fake다. 빠뜨리면 fake 응답이나 다른 후보가 모델 이름 아래 잰다.
    assert option(fake.command, "--embedder") == "openai_compatible"
    assert option(fake.command, "--llm-provider") == "openai_compatible"
    assert option(fake.command, "--case-interval-seconds") == "0.0"
    assert fake.env["WHATFROM_RECOMMEND_LLM_API"] == "anthropic"
    assert fake.env["WHATFROM_RECOMMEND_LLM_BASE_URL"] == "https://api.anthropic.com/v1"
    assert fake.env["WHATFROM_RECOMMEND_LLM_MODEL"] == "claude-sonnet-5-5"
    assert fake.env["WHATFROM_RECOMMEND_LLM_API_KEY"] == "anthropic-key"
    assert json.loads(fake.env["WHATFROM_RECOMMEND_LLM_EXTRA_BODY"]) == {
        "output_config": {"effort": "low"}
    }
    assert fake.env["WHATFROM_LLM_TIMEOUT_SECONDS"] == "120"
    assert copied == tmp_path / "results" / "sonnet-low-r1.json"
    assert json.loads(copied.read_text()) == fake.document


def test_gemini_waits_between_cases_and_the_interval_can_be_raised(tmp_path):
    default = FakeEval(result("gemini-low"))
    run_measure("gemini-low", tmp_path, default, environ={"GEMINI_API_KEY": "g"})
    raised = FakeEval(result("gemini-minimal"))
    run_measure("gemini-minimal", tmp_path, raised, environ={"GEMINI_API_KEY": "g"}, interval=12.0)

    assert option(default.command, "--case-interval-seconds") == "6.0"
    assert option(raised.command, "--case-interval-seconds") == "12.0"


def test_a_result_on_a_different_input_is_not_copied(tmp_path):
    fake = FakeEval(result("luna-low", prompt_suffix=" with newer evidence"))

    with pytest.raises(SystemExit, match="q1, q2"):
        run_measure("luna-low", tmp_path, fake)

    assert not (tmp_path / "results" / "luna-low-r1.json").exists()


def test_an_existing_round_is_not_overwritten(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "luna-low-r1.json").write_text("{}")
    fake = FakeEval(result("luna-low"))

    with pytest.raises(SystemExit, match="이미"):
        run_measure("luna-low", tmp_path, fake)

    assert fake.command == []


def test_a_missing_provider_key_stops_before_running(tmp_path):
    fake = FakeEval(result("luna-low"))

    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        run_measure("luna-low", tmp_path, fake, environ={})

    assert fake.command == []


def repository_row(name: str) -> Repository:
    return Repository(name=name, is_official=True, source_url="https://x.invalid", collected_at=NOW)


def test_the_snapshot_records_indexed_repositories_and_accepted_tag_digests(session: Session):
    """채점은 측정 시점 DB의 digest로 이름이 다른 같은 이미지를 정답으로 친다."""
    session.add_all([repository_row("python"), repository_row("node")])
    document = Document(
        repository="python",
        doc_type="readme",
        section_title="A",
        content="본문",
        source_url="https://x.invalid",
        collected_at=NOW,
    )
    session.add(document)
    session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id, chunk_index=0, content="본문", embedding=[0.0] * 1024
        )
    )
    session.add_all(
        [
            ImageTag(
                repository="python", tag="3.13-slim", manifest_digest="sha256:a", collected_at=NOW
            ),
            ImageTag(repository="python", tag="3.13", manifest_digest=None, collected_at=NOW),
            ImageTag(repository="node", tag="22", manifest_digest="sha256:n", collected_at=NOW),
        ]
    )
    session.flush()

    recorded = snapshot(session, ["python:3.13-slim", "python:3.13", "python:9-missing"])

    assert recorded == {
        "repositories": {"node": 0, "python": 1},
        "digests": {"python:3.13": None, "python:3.13-slim": "sha256:a", "python:9-missing": None},
    }


SCHEMA_ANSWER = json.dumps(
    {"image": "python:3.13-slim", "reason": "r", "dockerfile": "FROM x", "alternatives": []}
)


def openai_body(finish_reason: str = "stop", content: str = SCHEMA_ANSWER, refusal=None) -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content, "refusal": refusal},
            }
        ],
        "usage": {
            "prompt_tokens": 20000,
            "completion_tokens": 900,
            "completion_tokens_details": {"reasoning_tokens": 300},
        },
    }


def anthropic_body(stop_reason: str = "end_turn") -> dict:
    return {
        "content": [{"type": "text", "text": SCHEMA_ANSWER}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 20000, "output_tokens": 900},
    }


def transport(status: int, body: dict | None = None, text: str = "") -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if body is not None:
            return httpx2.Response(status, json=body)
        return httpx2.Response(status, text=text)

    return httpx2.MockTransport(handler)


def check(config: str, mock: httpx2.MockTransport, key: str = "secret-key") -> dict:
    return precheck(CONFIGS[config], "the largest prompt", key, mock)


def test_a_successful_precheck_keeps_the_finish_reason_and_raw_usage():
    record = check("luna-low", transport(200, openai_body()))

    assert record["kind"] == "ok"
    assert record["finish_reason"] == "stop"
    assert record["usage"]["completion_tokens_details"] == {"reasoning_tokens": 300}
    assert record["reasoning_tokens"] == 300
    assert record["schema_ok"] is True
    assert record["error"] is None


def test_an_anthropic_precheck_reads_the_stop_reason():
    record = check("sonnet-min", transport(200, anthropic_body()))

    assert record["kind"] == "ok"
    assert record["finish_reason"] == "end_turn"
    assert record["usage"] == {"input_tokens": 20000, "output_tokens": 900}


@pytest.mark.parametrize(
    ("config", "body"),
    [
        ("luna-low", openai_body(finish_reason="length", content='{"image": "py')),
        ("sonnet-low", anthropic_body(stop_reason="max_tokens")),
    ],
)
def test_a_truncated_answer_is_recorded_as_truncated(config, body):
    record = check(config, transport(200, body))

    assert record["kind"] == "truncated"
    assert record["body"] is not None


@pytest.mark.parametrize(
    ("config", "body"),
    [
        ("luna-low", openai_body(content="", refusal="I can't help with that")),
        ("sonnet-low", anthropic_body(stop_reason="refusal")),
    ],
)
def test_a_refusal_is_recorded_as_refused(config, body):
    assert check(config, transport(200, body))["kind"] == "refused"


def test_a_rate_limit_keeps_the_response_body():
    """Gemini의 429 세 종류와 Anthropic 지출 상한은 본문으로만 구분된다."""
    record = check("gemini-low", transport(429, text='{"error": "RESOURCE_EXHAUSTED"}'))

    assert record["kind"] == "transient"
    assert record["status"] == 429
    assert "RESOURCE_EXHAUSTED" in record["body"]


def test_a_rejected_request_is_a_permanent_failure():
    assert check("sol-low", transport(400, text="unsupported"))["kind"] == "permanent"


def test_the_api_key_never_reaches_the_record():
    record = check("luna-low", transport(401, text="bad key"), key="sk-very-secret")

    assert "sk-very-secret" not in json.dumps(record)


def test_an_answer_outside_the_schema_is_recorded_as_a_schema_failure():
    record = check("grok-none", transport(200, openai_body(content='{"image": "python"}')))

    assert record["kind"] == "permanent"
    assert record["schema_ok"] is False


def test_an_anthropic_precheck_reads_the_thinking_tokens():
    body = anthropic_body()
    body["usage"]["output_tokens_details"] = {"thinking_tokens": 700}

    assert check("sonnet-low", transport(200, body))["reasoning_tokens"] == 700


def test_without_an_answer_there_is_no_schema_result():
    assert check("luna-low", transport(429, text="slow down"))["schema_ok"] is None
