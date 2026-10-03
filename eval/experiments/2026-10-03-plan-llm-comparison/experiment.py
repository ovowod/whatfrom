"""M1-a(조건 추출 단계의 LLM 비교) 실험의 사전 확인, 측정 실행, 집계.

사용(project root에서, EXP=eval/experiments/2026-10-03-plan-llm-comparison):
    uv run python $EXP/experiment.py precheck <설정|all>
    uv run python $EXP/experiment.py snapshot <before|after>
    uv run python $EXP/experiment.py measure <설정> <회차>
    uv run python $EXP/experiment.py summary

측정 설정과 집계 규칙은 같은 폴더의 plan_llm.py에, 절차는 .scratch/plan-llm-comparison/spec.md에
있다. 공급자 API key는 MOONSHOT_API_KEY, OPENAI_API_KEY, XAI_API_KEY, GEMINI_API_KEY에서 읽는다.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx2
from plan_llm import CONFIGS, Config, InvalidResults, Run, validate
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from whatfrom.core.httpclient import RemoteCallError
from whatfrom.core.models import Document, DocumentChunk, Repository
from whatfrom.recommend.llm import OpenAICompatibleProvider
from whatfrom.recommend.planner import extract_plan

# 운영과 같은 timeout. 환경 변수에 다른 값이 있어도 이 값으로 잰다.
TIMEOUT_SECONDS = 120.0


class _Recording(httpx2.BaseTransport):
    """마지막 응답을 남기는 transport. provider는 원본 usage와 상태 코드를 밖에 주지 않는다."""

    def __init__(self, inner: httpx2.BaseTransport) -> None:
        self._inner = inner
        self.last: httpx2.Response | None = None

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        response = self._inner.handle_request(request)
        response.read()
        self.last = response
        return response


def precheck(
    config: Config,
    question: str,
    repositories: list[str],
    api_key: str,
    transport: httpx2.BaseTransport | None = None,
) -> dict:
    """조건 추출 단계를 평가와 같은 provider 코드로 한 번 부르고 결과를 기록으로 돌려준다.

    kind는 ok, transient(429·5xx·timeout·연결 실패), permanent(그 밖의 실패) 중 하나다.
    재시도하지 않는다. 일시적 실패는 시간을 두고 사전 확인을 다시 한다.
    """
    recording = _Recording(transport or httpx2.HTTPTransport())
    client = httpx2.Client(
        transport=recording,
        timeout=httpx2.Timeout(TIMEOUT_SECONDS, connect=3.0, read=TIMEOUT_SECONDS, write=10.0),
    )
    provider = OpenAICompatibleProvider(
        base_url=config.base_url,
        model=config.model,
        api_key=api_key,
        client=client,
        max_retries=0,
        extra_body=config.extra_body,
    )
    record: dict = {"config": config.name, "status": None, "error": None}
    try:
        extract_plan(provider, question, repositories)
    except RemoteCallError as exc:
        record["error"] = str(exc)
    response = recording.last
    if response is not None:
        record["status"] = response.status_code
    record["kind"] = _kind(record["error"], response)
    record["usage"] = _usage(response)
    if response is not None and response.status_code >= 400:
        record["body"] = response.text[:2000]
    return record


def _kind(error: str | None, response: httpx2.Response | None) -> str:
    """200 응답이 schema 검증에 실패한 것도 permanent다. 다시 불러도 같은 모델이 같은
    schema를 받으므로 시간을 두고 다시 할 일이 아니다. status 200으로 4xx와 구별된다.
    """
    if error is None:
        return "ok"
    if response is None or response.status_code == 429 or response.status_code >= 500:
        return "transient"
    return "permanent"


def _usage(response: httpx2.Response | None) -> dict | None:
    if response is None or response.status_code >= 400:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("usage") if isinstance(body, dict) else None


def repository_snapshot(session: Session) -> dict[str, int]:
    """repository마다 embedding이 있는 chunk를 가진 문서 수. 색인되지 않은 repository는 0이다.

    조건 추출 단계의 prompt는 DB의 repository 목록 전체를 쓴다. 측정 전후 이 값이 다르면
    모델마다 다른 입력을 받은 것이다.
    """
    indexed = dict(
        session.execute(
            select(Document.repository, func.count(func.distinct(Document.id)))
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.embedding.is_not(None))
            .group_by(Document.repository)
        ).all()
    )
    names = session.execute(select(Repository.name).order_by(Repository.name)).scalars()
    return {name: indexed.get(name, 0) for name in names}


def api_key_for(config: Config, environ: Mapping[str, str]) -> str:
    api_key = environ.get(config.key_env)
    if not api_key:
        raise SystemExit(f"{config.key_env}가 없다")
    return api_key


def _stage_env(stage: str, config: Config, api_key: str) -> dict[str, str]:
    prefix = f"WHATFROM_{stage}_LLM_"
    return {
        f"{prefix}BASE_URL": config.base_url,
        f"{prefix}MODEL": config.model,
        f"{prefix}API_KEY": api_key,
        # 빈 값은 설정하지 않은 것으로 본다. .env에 다른 값이 있어도 이 값으로 덮어쓴다.
        f"{prefix}EXTRA_BODY": json.dumps(config.extra_body) if config.extra_body else "",
    }


def eval_command(config: Config, results_dir: Path) -> list[str]:
    # --llm-provider를 빠뜨리면 기본값 fake로 돌아 fake 응답을 모델 이름 아래 잰다.
    mode = ["--plan-only"] if config.mode == "plan-only" else []
    return [
        *["uv", "run", "python", "-m", "whatfrom.cli", "eval", *mode],
        *["--llm-provider", "openai_compatible", "--results-dir", str(results_dir)],
    ]


def _run_eval(command: list[str], env: dict[str, str], results_dir: Path) -> None:
    subprocess.run(command, env=env, check=True)


def measure(
    name: str,
    round_: int,
    *,
    environ: Mapping[str, str],
    case_ids: list[str],
    out_dir: Path,
    run_eval: Callable[[list[str], dict[str, str], Path], None] = _run_eval,
) -> Path:
    """측정 설정 하나를 한 회차 돌려 결과 JSON을 out_dir/<설정>-r<회차>.json으로 복사한다.

    기준선(kimi-max)은 전체 평가로 두 단계 모두 kimi-k3를 쓴다. 나머지는 --plan-only다.
    측정 설정과 다르게 돌았거나 미측정 문항이 있는 결과는 복사하지 않는다.
    """
    config = CONFIGS[name]
    target = out_dir / f"{name}-r{round_}.json"
    if target.exists():
        raise SystemExit(f"{target}가 이미 있다. 회차 번호를 확인한다")
    api_key = api_key_for(config, environ)

    env = dict(environ)
    env |= _stage_env("PLAN", config, api_key)
    if config.mode == "full":
        env |= _stage_env("RECOMMEND", config, api_key)
    env["WHATFROM_LLM_TIMEOUT_SECONDS"] = str(int(TIMEOUT_SECONDS))

    with tempfile.TemporaryDirectory() as tmp:
        results_dir = Path(tmp)
        run_eval(eval_command(config, results_dir), env, results_dir)
        (produced,) = results_dir.glob("*.json")
        document = json.loads(produced.read_text(encoding="utf-8"))
        try:
            validate(Run(name, round_, document), case_ids)
        except InvalidResults as exc:
            raise SystemExit(f"복사하지 않는다: {exc}") from exc
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, target)
    return target


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESULTS = HERE / "results"
PRECHECK_LOG = HERE / "precheck.jsonl"


def _snapshot_path(when: str) -> Path:
    return HERE / f"repositories-{when}.json"


def main() -> None:
    # script로 실행할 때만 필요한 것들. 테스트가 import할 때 DB나 PyYAML을 건드리지 않는다.
    import argparse
    import os
    from datetime import UTC, datetime

    from plan_llm import render, summarize

    from whatfrom.api import repository_names
    from whatfrom.core.config import settings
    from whatfrom.core.db import make_engine, session_scope
    from whatfrom.eval.goldenset import load_goldenset

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_precheck = sub.add_parser("precheck")
    p_precheck.add_argument("config", choices=[*CONFIGS, "all"])
    p_snapshot = sub.add_parser("snapshot")
    p_snapshot.add_argument("when", choices=["before", "after"])
    p_measure = sub.add_parser("measure")
    p_measure.add_argument("config", choices=list(CONFIGS))
    p_measure.add_argument("round", type=int)
    sub.add_parser("summary")
    args = parser.parse_args()

    goldenset = load_goldenset(ROOT / "eval" / "goldenset.yaml")
    case_ids = [case.id for case in goldenset.cases]

    if args.command == "precheck":
        with session_scope(make_engine(settings.database_url)) as session:
            repositories = repository_names(session)
        names = list(CONFIGS) if args.config == "all" else [args.config]
        for name in names:
            config = CONFIGS[name]
            api_key = api_key_for(config, os.environ)
            record = precheck(config, goldenset.cases[0].question, repositories, api_key)
            record["at"] = datetime.now(UTC).isoformat(timespec="seconds")
            with PRECHECK_LOG.open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{name}: {record['kind']} {record['status'] or ''} {record['error'] or ''}")
            if record["usage"] is not None:
                print(f"  usage: {json.dumps(record['usage'])}")
    elif args.command == "snapshot":
        target = _snapshot_path(args.when)
        if target.exists():
            raise SystemExit(f"{target}가 이미 있다")
        with session_scope(make_engine(settings.database_url)) as session:
            snapshot = repository_snapshot(session)
        target.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        print(f"{target} 저장: repository {len(snapshot)}개")
    elif args.command == "measure":
        if not _snapshot_path("before").exists():
            raise SystemExit("측정 전 repository 목록이 없다. snapshot before를 먼저 돌린다")
        target = measure(
            args.config, args.round, environ=os.environ, case_ids=case_ids, out_dir=RESULTS
        )
        print(f"{target} 저장")
    else:
        runs = []
        for path in sorted(RESULTS.glob("*-r*.json")):
            name, _, round_ = path.stem.rpartition("-r")
            runs.append(Run(name, int(round_), json.loads(path.read_text(encoding="utf-8"))))
        snapshots = tuple(
            json.loads(_snapshot_path(when).read_text(encoding="utf-8"))
            for when in ("before", "after")
        )
        try:
            summary = summarize(runs, case_ids, snapshots)
        except InvalidResults as exc:
            raise SystemExit(f"집계하지 않는다: {exc}") from exc
        text = render(summary)
        (HERE / "summary.md").write_text(text + "\n", encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
