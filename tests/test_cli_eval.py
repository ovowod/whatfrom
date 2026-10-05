# tests/test_cli_eval.py
"""러너가 실행 메타에 남기는 golden set 출처와, 측정/미측정을 가르는 조회를 확인한다.

cli.py는 stage들을 조합하는 지점이라 단위 테스트가 아니라 여기서 본다. 측정
문항이 0이 되도록(색인되지 않은 repository를 요구하는 문항만) golden set을 짜서 LLM도
임베딩도 타지 않게 한다 — 보려는 것은 점수가 아니라 메타다.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from whatfrom import cli
from whatfrom.cli import accepted_digests, cmd_eval, indexed_repositories
from whatfrom.collect.hub import TagRow, VariantRow
from whatfrom.collect.store import upsert_tags
from whatfrom.core.config import Settings
from whatfrom.core.contracts import Recommendation, SearchPlan
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository
from whatfrom.index.indexer import index_readme
from whatfrom.recommend.llm import FakeLLMProvider, LLMProvider, get_provider

NOW = datetime(2026, 9, 12, tzinfo=UTC)

GOLDENSET = """
version: 1
verified_on: 2026-09-12
cases:
  - id: not-indexed
    question: 색인되지 않은 repository를 요구해 미측정으로 빠진다
    requires_repositories: [there-is-no-such-repository]
    accept: [there-is-no-such-repository:1]
    expected_plan: {}
    rationale:
      note: 근거
      sources: [https://example.invalid/doc]
"""


@pytest.fixture(autouse=True)
def shared_engine(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """러너가 새 엔진을 열지 않고 테스트용 엔진을 쓰게 한다.

    cmd_eval은 프로세스가 곧 끝나는 CLI라 엔진을 닫지 않는다. 테스트 안에서
    부르면 커넥션을 쥔 풀이 그대로 GC되어 다른 테스트에 ResourceWarning으로
    떨어진다.
    """
    monkeypatch.setattr(cli, "make_engine", lambda _url: engine)


def run_document(
    goldenset: Path,
    results_dir: Path,
    retrieval_only: bool = True,
    llm_provider: str = "fake",
    plan_only: bool = False,
    plans: str | None = None,
    case_interval_seconds: float = 0.0,
) -> dict:
    args = argparse.Namespace(
        goldenset=str(goldenset),
        results_dir=str(results_dir),
        tags="",
        retrieval_only=retrieval_only,
        plan_only=plan_only,
        plans=plans,
        embedder="fake",
        llm_provider=llm_provider,
        database_url="",
        case_interval_seconds=case_interval_seconds,
    )
    cmd_eval(args)
    result = next(iter(results_dir.glob("*.json")))
    return json.loads(result.read_text(encoding="utf-8"))


def run(goldenset: Path, results_dir: Path) -> dict:
    return run_document(goldenset, results_dir)["meta"]


def test_run_metadata_records_the_goldenset_content_hash(tmp_path: Path) -> None:
    goldenset = tmp_path / "goldenset.yaml"
    goldenset.write_text(GOLDENSET, encoding="utf-8")

    meta = run(goldenset, tmp_path / "results")

    assert meta["goldenset_sha256"] == hashlib.sha256(goldenset.read_bytes()).hexdigest()


def test_editing_a_label_changes_the_hash_though_the_version_does_not(tmp_path: Path) -> None:
    """version은 라벨을 고쳐도 그대로다. 그것만 남기면 비교할 수 없는 두 실행이
    같은 golden set을 돌린 것처럼 보인다."""
    first = tmp_path / "first.yaml"
    first.write_text(GOLDENSET, encoding="utf-8")
    second = tmp_path / "second.yaml"
    second.write_text(
        GOLDENSET.replace(
            "accept: [there-is-no-such-repository:1]", "accept: [there-is-no-such-repository:2]"
        ),
        encoding="utf-8",
    )

    before = run(first, tmp_path / "before")
    after = run(second, tmp_path / "after")

    assert before["goldenset_version"] == after["goldenset_version"]
    assert before["goldenset_sha256"] != after["goldenset_sha256"]


def test_run_metadata_records_when_the_goldenset_was_verified(tmp_path: Path) -> None:
    """golden set의 latest·LTS 주장이 언제 기준인지 결과 파일만 보고 알 수 있어야 한다."""
    goldenset = tmp_path / "goldenset.yaml"
    goldenset.write_text(GOLDENSET, encoding="utf-8")

    meta = run(goldenset, tmp_path / "results")

    assert meta["goldenset_verified_on"] == "2026-09-12"


def test_run_metadata_records_each_stage_llm_without_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """두 단계가 다른 모델을 쓰면 결과 파일만 보고 어느 조합인지 알 수 있어야 한다."""
    config = Settings(
        _env_file=None,
        llm_base_url="http://common.invalid/v1",
        llm_model="common-model",
        WHATFROM_LLM_API_KEY="secret-common",
        plan_llm_model="plan-model",
        plan_llm_api_key="secret-plan",
        recommend_llm_extra_body={"reasoning_effort": "low"},
    )
    monkeypatch.setattr(cli, "settings", config)
    goldenset = tmp_path / "goldenset.yaml"
    goldenset.write_text(GOLDENSET, encoding="utf-8")

    document = run_document(
        goldenset, tmp_path / "results", retrieval_only=False, llm_provider="openai_compatible"
    )

    assert document["meta"]["llm_stages"] == {
        "plan": {
            "base_url": "http://common.invalid/v1",
            "model": "plan-model",
            "extra_body": None,
            "api": "openai_compatible",
        },
        "recommend": {
            "base_url": "http://common.invalid/v1",
            "model": "common-model",
            "extra_body": {"reasoning_effort": "low"},
            "api": "openai_compatible",
        },
    }
    assert "secret" not in json.dumps(document)


@pytest.mark.parametrize("retrieval_only", [True, False])
def test_both_modes_report_and_save_the_random_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], retrieval_only: bool
) -> None:
    """무작위 선택 대조군은 후보만 있으면 계산되므로 두 모드가 각자 출력하고 저장한다.

    측정 문항이 없으면 0%가 아니라 미측정이다.
    """
    goldenset = tmp_path / "goldenset.yaml"
    goldenset.write_text(GOLDENSET, encoding="utf-8")

    document = run_document(goldenset, tmp_path / "results", retrieval_only)

    assert document["random_baseline"] == {"expected": None, "total": 0}
    assert "무작위 선택 대조군" in capsys.readouterr().out


def _repository(name: str) -> Repository:
    return Repository(
        name=name, is_official=True, source_url=f"https://hub.docker.com/_/{name}", collected_at=NOW
    )


def test_a_collected_but_unindexed_repository_is_not_available(session: Session) -> None:
    """수집만 되고 색인되지 않은 repository는 미측정으로 빠져야 한다.

    collect와 index는 별도 단계다. 색인되지 않은 repository는 청크가 없어 검색이 늘
    빈 결과를 주므로, 측정에 넣으면 그 문항들이 전부 0점이 되어 색인 커버리지가
    검색 품질로 둔갑한다.
    """
    session.add(_repository("collected-only"))
    session.flush()

    assert "collected-only" not in indexed_repositories(session)


def test_an_indexed_repository_is_available(session: Session) -> None:
    """반대쪽도 못 박는다. 아무것도 돌려주지 않으면 모든 문항이 미측정이 된다."""
    session.add(_repository("indexed"))
    document = Document(
        repository="indexed",
        doc_type="readme",
        section_title="Image Variants",
        content="본문",
        source_url="https://hub.docker.com/_/indexed",
        collected_at=NOW,
    )
    session.add(document)
    session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id, chunk_index=0, content="본문", embedding=[0.0] * 1024
        )
    )
    session.flush()

    assert "indexed" in indexed_repositories(session)


def test_cli_imports_without_pyyaml() -> None:
    """PyYAML은 dev 의존성이다. eval 외의 명령은 PyYAML 없이도 떠야 한다."""
    code = (
        "import sys; sys.modules['yaml'] = None; "
        "import whatfrom.cli as cli; cli.build_parser().parse_args(['collect', 'python'])"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_accepted_digests_reads_the_digests_of_the_accepted_tags(session: Session) -> None:
    """accept에 없는 태그, 수집되지 않은 태그, digest가 없는 태그는 집합에 들어가지 않는다."""
    session.add(_repository("temurin"))
    session.flush()
    for tag, digest in [
        ("25-jdk-noble", "sha256:jdk"),
        ("25-jre", "sha256:jre"),
        ("24", None),
    ]:
        session.add(
            ImageTag(repository="temurin", tag=tag, manifest_digest=digest, collected_at=NOW)
        )
    session.flush()

    digests = accepted_digests(
        session, ["temurin:25-jdk-noble", "temurin:24", "temurin:not-collected"]
    )

    assert digests == frozenset({"sha256:jdk"})


class BrokenEmbedder:
    dimension = 1024

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RemoteCallError("embedding down")


def _fixed_session(session: Session):
    @contextmanager
    def open_fixed() -> Generator[Session]:
        yield session

    return open_fixed


def _seed_python(session: Session) -> None:
    """추천 경로가 LLM #2까지 가도록 색인된 repository와 태그 하나를 넣는다."""
    session.add(_repository("python"))
    session.flush()
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-slim",
                manifest_digest="sha256:aaa",
                last_pushed_at=NOW,
                variants=(VariantRow("linux", "amd64", "", "", "sha256:bbb", 1),),
            )
        ],
        NOW,
    )
    readme = "# Image Variants\n\n## `python:<version>-slim`\n\npython slim image.\n"
    index_readme(session, "python", readme, "https://example.invalid/python", FakeEmbedder(), NOW)
    session.flush()


RECOMMENDATION = Recommendation(image="python:3.13-slim", reason="ok")


def test_timed_recommendation_times_every_stage_and_keeps_the_advise_prompt(
    session: Session,
) -> None:
    _seed_python(session)

    response, trace = cli.timed_recommendation(
        _fixed_session(session),
        FakeEmbedder(),
        FakeLLMProvider(recommendation=RECOMMENDATION),
        "python slim image",
    )

    assert response.recommended is not None
    assert None not in (
        trace.seconds_total,
        trace.seconds_embedding,
        trace.seconds_plan,
        trace.seconds_advise,
    )
    assert trace.seconds_total >= trace.seconds_advise
    assert "python:3.13-slim" in trace.advise_prompt


def test_timed_recommendation_times_a_failed_advise_call(session: Session) -> None:
    """LLM #2가 시간 초과로 실패해도 그 호출에 걸린 시간과 보낸 프롬프트가 남는다."""
    _seed_python(session)

    response, trace = cli.timed_recommendation(
        _fixed_session(session),
        FakeEmbedder(),
        FakeLLMProvider(error=RemoteCallError("read timed out")),
        "python slim image",
    )

    assert response.recommendation is None
    assert trace.seconds_advise is not None
    assert "python:3.13-slim" in trace.advise_prompt


def test_timed_recommendation_leaves_unreached_stages_empty(session: Session) -> None:
    """색인이 비어 있으면 후보가 없어 LLM #2까지 가지 않는다. 부르지 않은 단계는 None이다."""
    response, trace = cli.timed_recommendation(
        _fixed_session(session), FakeEmbedder(), FakeLLMProvider(), "질문"
    )

    assert response.recommendation is None
    assert trace.seconds_total >= trace.seconds_embedding >= 0.0
    assert trace.seconds_plan is not None
    assert (trace.seconds_advise, trace.advise_prompt) == (None, None)


def test_timed_recommendation_does_not_carry_over_the_previous_question(session: Session) -> None:
    """같은 공급자로 두 문항을 이어 돌려도, 두 번째 기록에 첫 문항의 시간과 prompt가 없다.

    두 번째 문항은 embedding에서 멈춰 LLM을 부르지 않는다. proxy를 문항 사이에 재사용하면
    첫 문항이 남긴 값이 그대로 보인다.
    """
    _seed_python(session)
    provider = FakeLLMProvider(recommendation=RECOMMENDATION)

    _, first = cli.timed_recommendation(
        _fixed_session(session), FakeEmbedder(), provider, "python slim image"
    )
    _, second = cli.timed_recommendation(
        _fixed_session(session), BrokenEmbedder(), provider, "node alpine image"
    )

    assert first.advise_prompt is not None
    assert (second.seconds_plan, second.seconds_advise, second.advise_prompt) == (None, None, None)


def test_timed_recommendation_times_a_failed_embedding(session: Session) -> None:
    response, trace = cli.timed_recommendation(
        _fixed_session(session), BrokenEmbedder(), FakeLLMProvider(), "질문"
    )

    assert response.degraded is True
    assert trace.seconds_embedding is not None
    assert (trace.seconds_plan, trace.seconds_advise) == (None, None)


def _recording_provider(log: list) -> LLMProvider:
    """두 단계 모두 성공하는 OpenAI 호환 provider. 호출 기록을 log에 남긴다."""
    plan = json.dumps({"repository": "python"})
    recommendation = RECOMMENDATION.model_dump_json()

    def handler(request: httpx2.Request) -> httpx2.Response:
        stage = json.loads(request.content)["response_format"]["json_schema"]["name"]
        content = plan if stage == "search_plan" else recommendation
        usage = {"prompt_tokens": 10, "completion_tokens": 5}
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": content}}], "usage": usage}
        )

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return get_provider(
        "openai_compatible", config=Settings(_env_file=None), client=client, on_call=log.append
    )


def test_timed_recommendation_keeps_the_llm_calls_of_its_question_only(session: Session) -> None:
    """provider는 실행 전체가 함께 쓴다. 문항마다 그 문항의 호출만 남아야 한다."""
    _seed_python(session)
    log: list = []
    provider = _recording_provider(log)

    _, first = cli.timed_recommendation(
        _fixed_session(session), FakeEmbedder(), provider, "첫 질문", call_log=log
    )
    _, second = cli.timed_recommendation(
        _fixed_session(session), FakeEmbedder(), provider, "둘째 질문", call_log=log
    )

    for trace in (first, second):
        assert [call["stage"] for call in trace.llm_calls] == ["plan", "recommend"]
        assert all(call["ok"] for call in trace.llm_calls)
        assert trace.llm_calls[0]["input_tokens"] == 10


PLAN_GOLDENSET = """
version: 1
verified_on: 2026-09-12
cases:
  - id: python-arm64
    question: ARM64에서 도는 python 이미지
    requires_repositories: [python]
    accept: [python:3.13-slim]
    expected_plan:
      architectures: [arm64]
    rationale:
      note: 근거
      sources: [https://example.invalid/doc]
  - id: not-indexed
    question: 색인되지 않은 repository를 요구해 미측정으로 빠진다
    requires_repositories: [there-is-no-such-repository]
    accept: [there-is-no-such-repository:1]
    expected_plan: {}
    rationale:
      note: 근거
      sources: [https://example.invalid/doc]
"""


def _use_test_session(monkeypatch: pytest.MonkeyPatch, session: Session, provider) -> None:
    """cmd_eval이 테스트 세션의 데이터를 보고, 넘긴 provider를 쓰게 한다."""
    _seed_python(session)
    monkeypatch.setattr(cli, "session_factory", lambda _engine: _fixed_session(session))
    monkeypatch.setattr(cli, "get_provider", lambda *_args, **_kwargs: provider)


def _plan_goldenset(tmp_path: Path) -> Path:
    goldenset = tmp_path / "goldenset.yaml"
    goldenset.write_text(PLAN_GOLDENSET, encoding="utf-8")
    return goldenset


def _run_plan_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session, provider
) -> dict:
    """--plan-only로 돌린다. 임베더가 불리면 실패하도록 BrokenEmbedder를 넣는다."""
    _use_test_session(monkeypatch, session, provider)
    monkeypatch.setattr(cli, "get_embedder", lambda _name: BrokenEmbedder())
    return run_document(
        _plan_goldenset(tmp_path),
        tmp_path / "results",
        retrieval_only=False,
        llm_provider="openai_compatible",
        plan_only=True,
    )


def test_plan_only_scores_extraction_without_embedding_or_the_recommend_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    provider = FakeLLMProvider(plan=SearchPlan(repository="python", architectures=["arm64"]))

    document = _run_plan_only(tmp_path, monkeypatch, session, provider)

    metrics = {m["label"]: (m["hits"], m["total"]) for m in document["metrics"]}
    assert metrics == {"repository 추출 일치율": (1, 1), "조건 추출 일치율": (1, 1)}
    assert provider.calls == []
    [case] = document["cases"]
    assert case["plan"]["architectures"] == ["arm64"]
    assert case["seconds_plan"] is not None
    assert document["skipped"] == [
        {"case_id": "not-indexed", "missing": ["there-is-no-such-repository"]}
    ]
    assert document["meta"]["mode"] == "plan-only"
    assert document["meta"]["llm_stages"]["recommend"] is None


def test_plan_only_counts_a_failed_extraction_as_a_miss_on_both_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    provider = FakeLLMProvider(plan_error=RemoteCallError("plan down"))

    document = _run_plan_only(tmp_path, monkeypatch, session, provider)

    metrics = {m["label"]: (m["hits"], m["total"]) for m in document["metrics"]}
    assert metrics == {"repository 추출 일치율": (0, 1), "조건 추출 일치율": (0, 1)}
    assert document["cases"][0]["plan"] is None


def test_plan_only_cannot_be_combined_with_retrieval_only() -> None:
    assert cli.build_parser().parse_args(["eval", "--plan-only"]).plan_only is True
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["eval", "--plan-only", "--retrieval-only"])


ARM64_PLAN = SearchPlan(repository="python", architectures=["arm64"])


def _full_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session) -> Path:
    """전체 모드로 한 번 돌려 고정할 결과 파일을 만든다."""
    provider = FakeLLMProvider(recommendation=RECOMMENDATION, plan=ARM64_PLAN)
    _use_test_session(monkeypatch, session, provider)
    run_document(
        _plan_goldenset(tmp_path),
        tmp_path / "baseline",
        retrieval_only=False,
        llm_provider="openai_compatible",
    )
    return next((tmp_path / "baseline").glob("*.json"))


def _run_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plans: Path, provider) -> dict:
    monkeypatch.setattr(cli, "get_provider", lambda *_args, **_kwargs: provider)
    return run_document(
        _plan_goldenset(tmp_path),
        tmp_path / "fixed",
        retrieval_only=False,
        llm_provider="openai_compatible",
        plans=str(plans),
    )


def _plan_must_not_be_called() -> FakeLLMProvider:
    return FakeLLMProvider(
        recommendation=RECOMMENDATION, plan_error=RemoteCallError("plan must not be called")
    )


def test_fixed_plans_skip_the_plan_stage_and_build_the_same_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    plans = _full_run(tmp_path, monkeypatch, session)
    baseline = json.loads(plans.read_text(encoding="utf-8"))
    provider = _plan_must_not_be_called()

    document = _run_fixed(tmp_path, monkeypatch, plans, provider)

    assert provider.plan_calls == []
    assert len(provider.calls) == 1
    [case], [before] = document["cases"], baseline["cases"]
    assert case["plan"] == before["plan"]
    assert case["candidate_images"] == before["candidate_images"]
    assert document["meta"]["mode"] == "fixed-plans"
    assert document["meta"]["plans_from"] == plans.name
    assert document["meta"]["llm_stages"]["plan"] is None
    labels = {m["label"] for m in document["metrics"]}
    assert "추천 정확도" in labels
    assert not labels & {"repository 추출 일치율", "조건 추출 일치율"}


def test_a_recorded_extraction_failure_stays_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    """같은 입력을 주려는 것이므로, 기준선에서 추출이 실패한 문항은 이번에도 실패다."""
    plans = _full_run(tmp_path, monkeypatch, session)
    recorded = json.loads(plans.read_text(encoding="utf-8"))
    recorded["cases"][0]["plan"] = None
    plans.write_text(json.dumps(recorded), encoding="utf-8")
    provider = _plan_must_not_be_called()

    document = _run_fixed(tmp_path, monkeypatch, plans, provider)

    [case] = document["cases"]
    assert case["plan"] is None
    assert any("검색 조건 추출에 실패" in note for note in case["notes"])
    assert provider.plan_calls == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda doc: doc["meta"].update(mode="retrieval-only"), "full"),
        (lambda doc: doc["meta"].update(goldenset_sha256="other"), "golden set"),
        (lambda doc: doc.update(cases=[]), "python-arm64"),
    ],
)
def test_a_mismatched_plans_file_stops_before_calling_any_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session, change, message: str
) -> None:
    plans = _full_run(tmp_path, monkeypatch, session)
    recorded = json.loads(plans.read_text(encoding="utf-8"))
    change(recorded)
    plans.write_text(json.dumps(recorded), encoding="utf-8")
    provider = _plan_must_not_be_called()

    with pytest.raises(SystemExit, match=message):
        _run_fixed(tmp_path, monkeypatch, plans, provider)

    assert provider.calls == []


def test_fixed_plans_cannot_be_combined_with_another_mode() -> None:
    assert cli.build_parser().parse_args(["eval", "--plans", "a.json"]).plans == "a.json"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["eval", "--plans", "a.json", "--plan-only"])


def test_the_case_interval_waits_only_between_cases_and_is_not_timed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: Session
) -> None:
    """공급자의 분 단위 한도를 피하려고 쉰다. 쉰 시간이 지연에 섞이면 모델 비교가 틀어진다."""
    goldenset = tmp_path / "goldenset.yaml"
    python_case = PLAN_GOLDENSET[PLAN_GOLDENSET.index("  - id: python-arm64") :]
    python_case = python_case[: python_case.index("  - id: not-indexed")]
    goldenset.write_text(
        PLAN_GOLDENSET + python_case.replace("python-arm64", "python-arm64-again"),
        encoding="utf-8",
    )
    _use_test_session(monkeypatch, session, FakeLLMProvider(plan=ARM64_PLAN))
    monkeypatch.setattr(cli, "get_embedder", lambda _name: BrokenEmbedder())
    real_sleep = cli.time.sleep
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(cli.time, "sleep", sleep)

    document = run_document(
        goldenset,
        tmp_path / "results",
        retrieval_only=False,
        llm_provider="openai_compatible",
        plan_only=True,
        case_interval_seconds=0.3,
    )

    assert waits == [0.3]
    assert [case["case_id"] for case in document["cases"]] == ["python-arm64", "python-arm64-again"]
    assert all(case["seconds_total"] < 0.3 for case in document["cases"])
    assert document["meta"]["case_interval_seconds"] == 0.3


def test_the_case_interval_defaults_to_zero_and_rejects_negative_or_infinite_values() -> None:
    assert cli.build_parser().parse_args(["eval"]).case_interval_seconds == 0.0
    for value in ("-1", "nan", "inf"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["eval", "--case-interval-seconds", value])
