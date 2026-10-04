"""M1-b(추천 단계의 LLM 비교) 실험의 사전 확인, 회차 전후 snapshot, 측정 실행, 집계.

사용(project root에서, EXP=eval/experiments/2026-10-04-recommend-llm-comparison):
    uv run python $EXP/recommend_experiment.py precheck <설정|all>
    uv run python $EXP/recommend_experiment.py snapshot <회차> <before|after>
    uv run python $EXP/recommend_experiment.py measure <설정> <회차> [--interval 초]
    uv run python $EXP/recommend_experiment.py summary

측정 설정과 판정 규칙은 같은 폴더의 recommend_llm.py에, 절차는
.scratch/recommend-llm-comparison/spec.md에 있다. 공급자 API key는 MOONSHOT_API_KEY,
OPENAI_API_KEY, XAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY를 셸 환경 변수나
project root의 .env에서 읽는다.
"""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx2
from recommend_llm import (
    CONFIGS,
    REFERENCE_NAME,
    Config,
    InvalidResults,
    Run,
    nearest_rank,
    render,
    summarize,
    validate,
)
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from whatfrom.core.contracts import split_image
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository
from whatfrom.recommend.advisor import SYSTEM_PROMPT
from whatfrom.recommend.llm import PROVIDERS

# 운영과 같은 timeout. 환경 변수에 다른 값이 있어도 이 값으로 잰다.
TIMEOUT_SECONDS = 120.0
# 입력이 가장 큰 문항(약 20K token). 출력 잘림과 첫 schema 처리 지연을 함께 확인한다.
PRECHECK_CASE = "temurin-17-jammy-pinned"


class _Recording(httpx2.BaseTransport):
    """마지막 응답을 남기는 transport. provider는 원본 응답을 밖에 주지 않는다."""

    def __init__(self, inner: httpx2.BaseTransport) -> None:
        self._inner = inner
        self.last: httpx2.Response | None = None

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        response = self._inner.handle_request(request)
        response.read()
        self.last = response
        return response


def precheck(
    config: Config, prompt: str, api_key: str, transport: httpx2.BaseTransport | None = None
) -> dict:
    """추천 단계를 평가와 같은 provider 코드로 한 번 부르고 결과를 기록으로 돌려준다.

    kind는 ok, truncated(출력 잘림), refused(거부), transient(429·5xx·timeout·연결 실패),
    permanent(그 밖의 실패) 중 하나다. 재시도하지 않는다.
    """
    recording = _Recording(transport or httpx2.HTTPTransport())
    client = httpx2.Client(
        transport=recording,
        timeout=httpx2.Timeout(TIMEOUT_SECONDS, connect=3.0, read=TIMEOUT_SECONDS, write=10.0),
    )
    provider = PROVIDERS[config.api](
        base_url=config.base_url,
        model=config.model,
        api_key=api_key,
        client=client,
        max_retries=0,
        extra_body=config.extra_body,
    )
    record: dict = {"config": config.name, "status": None, "error": None}
    try:
        provider.recommend(SYSTEM_PROMPT, prompt)
    except RemoteCallError as exc:
        record["error"] = str(exc)
    response = recording.last
    body = _json(response)
    finish_reason, refused = _ending(config, body)
    kind = _kind(record["error"], response, finish_reason, refused)
    usage = body.get("usage") if body is not None else None
    record["status"] = response.status_code if response is not None else None
    record["finish_reason"] = finish_reason
    record["kind"] = kind
    # 답이 왔는데 잘리거나 거부되지 않았을 때만 schema 검증 결과가 있다.
    record["schema_ok"] = True if kind == "ok" else False if kind == "permanent" and body else None
    record["usage"] = usage
    # 공급자마다 reasoning token을 두는 자리가 다르다. 평가와 같은 provider 규칙으로 읽는다.
    record["reasoning_tokens"] = PROVIDERS[config.api]._token_counts(usage)[2]
    record["body"] = response.text[:2000] if record["error"] and response is not None else None
    return record


def _json(response: httpx2.Response | None) -> dict | None:
    if response is None or response.status_code >= 400:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _ending(config: Config, body: dict | None) -> tuple[str | None, bool]:
    """답이 끝난 이유와 거부 여부. Anthropic은 stop_reason, OpenAI 호환은 choices에 있다."""
    if body is None:
        return None, False
    if config.api == "anthropic":
        stop_reason = body.get("stop_reason")
        return stop_reason, stop_reason == "refusal"
    try:
        choice = body["choices"][0]
        finish_reason = choice.get("finish_reason")
        refused = bool(choice["message"].get("refusal")) or finish_reason == "content_filter"
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, False
    return finish_reason, refused


def _kind(
    error: str | None, response: httpx2.Response | None, finish_reason: str | None, refused: bool
) -> str:
    """200 응답이 잘리거나 거부된 것은 그 이유로, 나머지 200 실패는 permanent로 둔다.

    다시 불러도 같은 모델이 같은 prompt를 받으므로 시간을 두고 다시 할 일이 아니다.
    """
    if finish_reason in ("length", "max_tokens"):
        return "truncated"
    if refused:
        return "refused"
    if error is None:
        return "ok"
    if response is None or response.status_code == 429 or response.status_code >= 500:
        return "transient"
    return "permanent"


def snapshot(session: Session, accept: list[str]) -> dict:
    """회차 전후에 비교할 DB 상태.

    repositories는 repository마다 embedding이 있는 chunk를 가진 문서 수다. digests는 golden set
    허용 정답 태그의 digest다. 채점은 측정 시점 DB의 digest로 이름이 다른 같은 이미지를 정답으로
    치므로, 추천 단계 prompt가 같아도 이 값이 바뀌면 채점이 달라진다.
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
    pairs = [parts for image in accept if (parts := split_image(image)) is not None]
    found: dict[str, str | None] = {}
    if pairs:
        rows = session.execute(
            select(ImageTag.repository, ImageTag.tag, ImageTag.manifest_digest).where(
                tuple_(ImageTag.repository, ImageTag.tag).in_(pairs)
            )
        ).all()
        found = {f"{repository}:{tag}": digest for repository, tag, digest in rows}
    return {
        "repositories": {name: indexed.get(name, 0) for name in names},
        "digests": {image: found.get(image) for image in sorted(set(accept))},
    }


def api_key_for(config: Config, environ: Mapping[str, str]) -> str:
    api_key = environ.get(config.key_env)
    if not api_key:
        raise SystemExit(f"{config.key_env}가 없다")
    return api_key


def _recommend_env(config: Config, api_key: str) -> dict[str, str]:
    prefix = "WHATFROM_RECOMMEND_LLM_"
    return {
        f"{prefix}API": config.api,
        f"{prefix}BASE_URL": config.base_url,
        f"{prefix}MODEL": config.model,
        f"{prefix}API_KEY": api_key,
        # 빈 값은 설정하지 않은 것으로 본다. .env에 다른 값이 있어도 이 값으로 덮어쓴다.
        f"{prefix}EXTRA_BODY": json.dumps(config.extra_body) if config.extra_body else "",
    }


def eval_command(reference: Path, results_dir: Path, interval: float) -> list[str]:
    # --embedder와 --llm-provider는 기본값이 fake다. 빠뜨리면 다른 후보나 fake 응답을 잰다.
    return [
        *["uv", "run", "python", "-m", "whatfrom.cli", "eval", "--plans", str(reference)],
        *["--embedder", "openai_compatible", "--llm-provider", "openai_compatible"],
        *["--case-interval-seconds", str(interval), "--results-dir", str(results_dir)],
    ]


def _run_eval(command: list[str], env: dict[str, str], results_dir: Path) -> None:
    subprocess.run(command, env=env, check=True)


def measure(
    name: str,
    round_: int,
    *,
    environ: Mapping[str, str],
    reference: Path,
    out_dir: Path,
    interval: float | None = None,
    run_eval: Callable[[list[str], dict[str, str], Path], None] = _run_eval,
) -> Path:
    """측정 설정 하나를 한 회차 돌려 결과 JSON을 out_dir/<설정>-r<회차>.json으로 복사한다.

    검색 조건은 reference에서 고정하고 추천 단계만 측정 설정으로 부른다. interval을 주지 않으면
    측정 설정의 값을 쓴다. 측정 설정이나 고정 입력과 다르게 돈 결과는 복사하지 않는다.
    """
    config = CONFIGS[name]
    target = out_dir / f"{name}-r{round_}.json"
    if target.exists():
        raise SystemExit(f"{target}가 이미 있다. 회차 번호를 확인한다")
    api_key = api_key_for(config, environ)
    reference_document = json.loads(reference.read_text(encoding="utf-8"))

    env = dict(environ) | _recommend_env(config, api_key)
    env["WHATFROM_LLM_TIMEOUT_SECONDS"] = str(int(TIMEOUT_SECONDS))
    case_interval = config.interval_seconds if interval is None else interval

    with tempfile.TemporaryDirectory() as tmp:
        results_dir = Path(tmp)
        run_eval(eval_command(reference, results_dir, case_interval), env, results_dir)
        (produced,) = results_dir.glob("*.json")
        document = json.loads(produced.read_text(encoding="utf-8"))
        try:
            validate(Run(name, round_, document), reference_document)
        except InvalidResults as exc:
            raise SystemExit(f"복사하지 않는다: {exc}") from exc
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, target)
    return target


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESULTS = HERE / "results"
SNAPSHOTS = HERE / "snapshots"
PRECHECK_LOG = HERE / "precheck.jsonl"
M1A_RESULTS = ROOT / "eval" / "experiments" / "2026-10-03-plan-llm-comparison" / "results"
REFERENCE = M1A_RESULTS / REFERENCE_NAME
# M1-a에서 고른 조건 추출 단계 설정. 참고 기준의 바탕에 그 p95를 더한다.
CHOSEN_PLAN_CONFIG = "luna-low"


def _snapshot_path(round_: int, when: str) -> Path:
    return SNAPSHOTS / f"r{round_}-{when}.json"


def chosen_plan_p95() -> float:
    """M1-a에서 고른 설정의 조건 추출 단계 p95. 재시도가 없는 회차만 쓴다(M1-a 집계와 같다)."""
    seconds = []
    for path in sorted(M1A_RESULTS.glob(f"{CHOSEN_PLAN_CONFIG}-r*.json")):
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        calls = [call for case in cases for call in case["llm_calls"] if call["stage"] == "plan"]
        if all(call["attempts"] == 1 for call in calls):
            seconds += [case["seconds_plan"] for case in cases]
    return nearest_rank(seconds, 0.95)


def main() -> None:
    # script로 실행할 때만 필요한 것들. 테스트가 import할 때 DB나 PyYAML을 건드리지 않는다.
    import argparse
    import os
    from datetime import UTC, datetime

    from dotenv import dotenv_values

    from whatfrom.core.config import settings
    from whatfrom.core.db import make_engine, session_scope
    from whatfrom.eval.goldenset import load_goldenset

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_precheck = sub.add_parser("precheck")
    p_precheck.add_argument("config", choices=[*CONFIGS, "all"])
    p_snapshot = sub.add_parser("snapshot")
    p_snapshot.add_argument("round", type=int)
    p_snapshot.add_argument("when", choices=["before", "after"])
    p_measure = sub.add_parser("measure")
    p_measure.add_argument("config", choices=list(CONFIGS))
    p_measure.add_argument("round", type=int)
    p_measure.add_argument("--interval", type=float, default=None, help="문항 사이에 쉴 초")
    sub.add_parser("summary")
    args = parser.parse_args()

    # 앱처럼 .env도 읽는다. 셸 환경 변수가 있으면 그 값이 이긴다.
    dotenv = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}
    environ = dotenv | dict(os.environ)
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))

    if args.command == "precheck":
        (prompt,) = [
            case["advise_prompt"] for case in reference["cases"] if case["case_id"] == PRECHECK_CASE
        ]
        names = list(CONFIGS) if args.config == "all" else [args.config]
        for name in names:
            config = CONFIGS[name]
            record = precheck(config, prompt, api_key_for(config, environ))
            record["at"] = datetime.now(UTC).isoformat(timespec="seconds")
            with PRECHECK_LOG.open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{name}: {record['kind']} {record['status'] or ''} {record['error'] or ''}")
            print(
                f"  finish_reason: {record['finish_reason']}, usage: {json.dumps(record['usage'])}"
            )
    elif args.command == "snapshot":
        target = _snapshot_path(args.round, args.when)
        if target.exists():
            raise SystemExit(f"{target}가 이미 있다")
        goldenset = load_goldenset(ROOT / "eval" / "goldenset.yaml")
        accept = [image for case in goldenset.cases for image in case.accept]
        with session_scope(make_engine(settings.database_url)) as session:
            recorded = snapshot(session, accept)
        SNAPSHOTS.mkdir(exist_ok=True)
        target.write_text(json.dumps(recorded, indent=2) + "\n", encoding="utf-8")
        print(f"{target} 저장: repository {len(recorded['repositories'])}개")
    elif args.command == "measure":
        if not _snapshot_path(args.round, "before").exists():
            raise SystemExit(f"{args.round}회차 측정 전 snapshot이 없다. snapshot을 먼저 돌린다")
        target = measure(
            args.config,
            args.round,
            environ=environ,
            reference=REFERENCE,
            out_dir=RESULTS,
            interval=args.interval,
        )
        print(f"{target} 저장")
    else:
        runs = []
        for path in sorted(RESULTS.glob("*-r*.json")):
            name, _, round_ = path.stem.rpartition("-r")
            runs.append(Run(name, int(round_), json.loads(path.read_text(encoding="utf-8"))))
        snapshots = {}
        for round_ in {run.round for run in runs}:
            paths = [_snapshot_path(round_, when) for when in ("before", "after")]
            if all(path.exists() for path in paths):
                before, after = (json.loads(p.read_text(encoding="utf-8")) for p in paths)
                snapshots[round_] = (before, after)
        cases = reference["cases"]
        request_p95 = nearest_rank([case["seconds_total"] for case in cases], 0.95)
        advise = [case["seconds_advise"] for case in cases if case["seconds_advise"] is not None]
        alt_request_p95 = nearest_rank(advise, 0.95) + chosen_plan_p95()
        try:
            summary = summarize(
                runs,
                reference,
                snapshots,
                request_p95=request_p95,
                alt_request_p95=alt_request_p95,
            )
        except InvalidResults as exc:
            raise SystemExit(f"집계하지 않는다: {exc}") from exc
        text = render(summary)
        (HERE / "summary.md").write_text(text + "\n", encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
