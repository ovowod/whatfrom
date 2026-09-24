# tests/test_cli_eval.py
"""러너가 실행 메타에 남기는 골든셋 출처와, 측정/미측정을 가르는 조회를 확인한다.

cli.py는 stage들을 조합하는 지점이라 단위 테스트가 아니라 여기서 본다. 측정
문항이 0이 되도록(색인되지 않은 리포를 요구하는 문항만) 골든셋을 짜서 LLM도
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

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from whatfrom import cli
from whatfrom.cli import accepted_digests, cmd_eval, indexed_repositories
from whatfrom.collect.hub import TagRow, VariantRow
from whatfrom.collect.store import upsert_tags
from whatfrom.core.contracts import Recommendation
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository
from whatfrom.index.indexer import index_readme
from whatfrom.recommend.llm import FakeLLMProvider

NOW = datetime(2026, 9, 12, tzinfo=UTC)

GOLDENSET = """
version: 1
verified_on: 2026-09-12
cases:
  - id: not-indexed
    question: 색인되지 않은 리포를 요구해 미측정으로 빠진다
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


def run_document(goldenset: Path, results_dir: Path, retrieval_only: bool = True) -> dict:
    args = argparse.Namespace(
        goldenset=str(goldenset),
        results_dir=str(results_dir),
        tags="",
        retrieval_only=retrieval_only,
        embedder="fake",
        llm_provider="fake",
        database_url="",
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
    같은 골든셋을 돌린 것처럼 보인다."""
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
    """골든셋의 latest·LTS 주장이 언제 기준인지 결과 파일만 보고 알 수 있어야 한다."""
    goldenset = tmp_path / "goldenset.yaml"
    goldenset.write_text(GOLDENSET, encoding="utf-8")

    meta = run(goldenset, tmp_path / "results")

    assert meta["goldenset_verified_on"] == "2026-09-12"


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
    """수집만 되고 색인되지 않은 리포는 미측정으로 빠져야 한다.

    collect와 index는 별도 단계다. 색인되지 않은 리포는 청크가 없어 검색이 늘
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
    """추천 경로가 LLM #2까지 가도록 색인된 리포지토리와 태그 하나를 넣는다."""
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


RECOMMENDATION = Recommendation(
    image="python:3.13-slim", reason="ok", dockerfile="FROM python:3.13-slim\n"
)


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
    assert response.recommendation.dockerfile == "FROM python:3.13-slim@sha256:aaa\n"
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


def test_timed_recommendation_times_a_failed_embedding(session: Session) -> None:
    response, trace = cli.timed_recommendation(
        _fixed_session(session), BrokenEmbedder(), FakeLLMProvider(), "질문"
    )

    assert response.degraded is True
    assert trace.seconds_embedding is not None
    assert (trace.seconds_plan, trace.seconds_advise) == (None, None)
