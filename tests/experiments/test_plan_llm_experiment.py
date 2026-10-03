"""M1-a 실험의 사전 확인, 측정 실행, repository 목록 기록.

실제 공급자 API는 부르지 않는다. 사전 확인은 가짜 transport로, 측정 실행은 평가를
대신하는 함수로 확인한다.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from sqlalchemy.orm import Session

from whatfrom.core.models import Document, DocumentChunk, Repository

EXPERIMENT = Path(__file__).parents[2] / "eval" / "experiments" / "2026-10-03-plan-llm-comparison"
sys.path.insert(0, str(EXPERIMENT))

from experiment import measure, precheck, repository_snapshot  # noqa: E402
from plan_llm import CONFIGS, stage_meta  # noqa: E402

NOW = datetime(2026, 10, 3, tzinfo=UTC)
PLAN = json.dumps(
    {
        "repository": "python",
        "version_prefix": None,
        "architectures": [],
        "distributions": [],
        "exclude_distributions": [],
        "max_size_mb": None,
    }
)
USAGE = {"prompt_tokens": 900, "completion_tokens": 40, "total_tokens": 940}


def transport(status: int, body: dict | None = None, text: str = "") -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if body is not None:
            return httpx2.Response(status, json=body)
        return httpx2.Response(status, text=text)

    return httpx2.MockTransport(handler)


def check(mock: httpx2.MockTransport, key: str = "secret-key") -> dict:
    return precheck(CONFIGS["luna-none"], "slim python?", ["python"], key, mock)


def test_a_successful_precheck_keeps_the_raw_usage():
    body = {"choices": [{"message": {"content": PLAN}}], "usage": USAGE}

    record = check(transport(200, body))

    assert record["config"] == "luna-none"
    assert record["kind"] == "ok"
    assert record["usage"] == USAGE


@pytest.mark.parametrize("status", [429, 503])
def test_a_rate_limit_or_server_error_is_a_transient_failure(status):
    record = check(transport(status, text="busy"))

    assert (record["kind"], record["status"]) == ("transient", status)


def test_a_timeout_is_a_transient_failure():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    record = check(httpx2.MockTransport(handler))

    assert record["kind"] == "transient"
    assert record["status"] is None


def test_a_schema_rejection_is_a_permanent_failure_with_the_response_body():
    error = {"error": {"message": "Invalid schema: 'default' is not permitted"}}

    record = check(transport(400, error))

    assert (record["kind"], record["status"]) == ("permanent", 400)
    assert "default" in record["body"]


def test_the_api_key_never_reaches_the_record():
    error = {"error": {"message": "bad key"}}

    record = check(transport(401, error), key="secret-key")

    assert "secret-key" not in json.dumps(record)


CASES = ["q1", "q2"]
KEYS = {"OPENAI_API_KEY": "openai-key", "MOONSHOT_API_KEY": "moonshot-key"}


def result(config: str, *, skipped: list | None = None) -> dict:
    """측정 설정대로 돈 것처럼 보이는 평가 결과 JSON."""
    settings = CONFIGS[config]
    stage = stage_meta(settings)
    return {
        "meta": {
            "mode": settings.mode,
            "llm_provider": "openai_compatible",
            "goldenset_sha256": "golden",
            "llm_stages": {"plan": stage, "recommend": stage if settings.mode == "full" else None},
        },
        "cases": [{"case_id": case_id} for case_id in CASES],
        "skipped": skipped or [],
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
        (results_dir / "2026-10-03T00-00-00.json").write_text(json.dumps(self.document))


def run_measure(config: str, tmp_path: Path, fake: FakeEval, environ: dict | None = None) -> Path:
    return measure(
        config,
        1,
        environ=KEYS if environ is None else environ,
        case_ids=CASES,
        out_dir=tmp_path,
        run_eval=fake,
    )


def test_a_plan_only_measurement_runs_the_real_provider_with_its_settings(tmp_path):
    fake = FakeEval(result("luna-none"))

    copied = run_measure("luna-none", tmp_path, fake)

    assert "--plan-only" in fake.command
    assert fake.command[fake.command.index("--llm-provider") + 1] == "openai_compatible"
    assert fake.env["WHATFROM_PLAN_LLM_BASE_URL"] == "https://api.openai.com/v1"
    assert fake.env["WHATFROM_PLAN_LLM_MODEL"] == "gpt-6-luna"
    assert fake.env["WHATFROM_PLAN_LLM_API_KEY"] == "openai-key"
    assert json.loads(fake.env["WHATFROM_PLAN_LLM_EXTRA_BODY"]) == {"reasoning_effort": "none"}
    assert fake.env["WHATFROM_LLM_TIMEOUT_SECONDS"] == "120"
    assert copied == tmp_path / "luna-none-r1.json"
    assert json.loads(copied.read_text()) == fake.document


def test_the_baseline_runs_the_full_evaluation_with_kimi_k3_in_both_stages(tmp_path):
    fake = FakeEval(result("kimi-max"))

    run_measure("kimi-max", tmp_path, fake)

    assert "--plan-only" not in fake.command
    for stage in ("PLAN", "RECOMMEND"):
        assert fake.env[f"WHATFROM_{stage}_LLM_MODEL"] == "kimi-k3"
        assert fake.env[f"WHATFROM_{stage}_LLM_API_KEY"] == "moonshot-key"
        # 빈 값은 설정하지 않은 것으로 본다. .env에 다른 값이 있어도 덮어써 운영과 같게 한다.
        assert fake.env[f"WHATFROM_{stage}_LLM_EXTRA_BODY"] == ""


def test_a_result_with_unmeasured_cases_is_not_copied(tmp_path):
    fake = FakeEval(result("luna-none", skipped=[{"case_id": "q3", "missing": ["node"]}]))

    with pytest.raises(SystemExit, match="미측정"):
        run_measure("luna-none", tmp_path, fake)

    assert not (tmp_path / "luna-none-r1.json").exists()


def test_an_existing_round_is_not_overwritten(tmp_path):
    (tmp_path / "luna-none-r1.json").write_text("{}")
    fake = FakeEval(result("luna-none"))

    with pytest.raises(SystemExit, match="이미"):
        run_measure("luna-none", tmp_path, fake)

    assert fake.command == []


def test_a_missing_provider_key_stops_before_running(tmp_path):
    fake = FakeEval(result("luna-none"))

    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        run_measure("luna-none", tmp_path, fake, environ={})

    assert fake.command == []


def repository_row(name: str) -> Repository:
    return Repository(name=name, is_official=True, source_url="https://x.invalid", collected_at=NOW)


def document_row(repository: str, title: str) -> Document:
    return Document(
        repository=repository,
        doc_type="readme",
        section_title=title,
        content="본문",
        source_url="https://x.invalid",
        collected_at=NOW,
    )


def test_the_snapshot_counts_indexed_documents_for_every_repository(session: Session):
    session.add_all([repository_row("python"), repository_row("node")])
    indexed, unindexed = document_row("python", "A"), document_row("python", "B")
    session.add_all([indexed, unindexed])
    session.flush()
    session.add(
        DocumentChunk(document_id=indexed.id, chunk_index=0, content="본문", embedding=[0.0] * 1024)
    )
    session.flush()

    assert repository_snapshot(session) == {"node": 0, "python": 1}
