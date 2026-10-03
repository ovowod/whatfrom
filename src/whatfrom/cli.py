# src/whatfrom/cli.py
import argparse
import hashlib
import json
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
from sqlalchemy import Engine, inspect, select, text, tuple_
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from whatfrom.eval.goldenset import GoldenCase, GoldenSet

from whatfrom.api import recommend_for_question, repository_names
from whatfrom.collect import docs
from whatfrom.collect.derive import DeriveOutcome, derive_repository
from whatfrom.collect.hub import HubClient, parse_repository
from whatfrom.collect.store import upsert_repository
from whatfrom.collect.sync import (
    OFFICIAL_REPOSITORIES,
    STOP_ERROR,
    CollectOutcome,
    collect_all,
)
from whatfrom.core.config import settings
from whatfrom.core.contracts import (
    Recommendation,
    RecommendResponse,
    SearchPlan,
    split_image,
)
from whatfrom.core.db import SessionFactory, make_engine, session_factory, session_scope
from whatfrom.core.embed import Embedder, get_embedder
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.core.models import Base, Document, DocumentChunk, ImageTag, Repository
from whatfrom.eval.timing import RunTrace, Timed
from whatfrom.index.indexer import index_readme
from whatfrom.recommend.llm import LLMCall, LLMProvider, get_provider, stage_llm
from whatfrom.recommend.planner import extract_plan
from whatfrom.recommend.verify import image_exists
from whatfrom.search.retrieval import (
    search_candidates_by_vector,
    search_chunks,
    search_chunks_by_vector,
)


def cmd_init_db(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    # create_all은 이미 있는 테이블을 말없이 건너뛴다. 무엇을 만들었는지 세어두지
    # 않으면 아무것도 안 하고도 만들었다고 말하게 된다.
    existing = set(inspect(engine).get_table_names())
    created = sorted(set(Base.metadata.tables) - existing)
    Base.metadata.create_all(engine)

    if created:
        print(f"created on {engine.url.database}: {', '.join(created)}")
    else:
        print(f"{engine.url.database} already has every table, created nothing")


def run_collect(
    engine: Engine,
    client: HubClient,
    repositories: Sequence[str],
    max_pages: int | None,
) -> int:
    outcomes = collect_all(engine, client, repositories, max_pages, datetime.now(UTC))
    print(_format_collect_summary(outcomes))
    # 실패를 종료 코드로 알린다. 스케줄러에 올렸을 때 조용히 묻히지 않게 한다.
    return 1 if any(outcome.stop_reason == STOP_ERROR for outcome in outcomes) else 0


def _format_collect_summary(outcomes: list[CollectOutcome]) -> str:
    lines = [
        f"{'repository':<16} {'pages':>5} {'seen':>6} {'new_or_changed':>14} {'derived':>7}  "
        f"{'stop':<12} {'seconds':>7}"
    ]
    for outcome in outcomes:
        lines.append(
            f"{outcome.repository:<16} {outcome.pages:>5} {outcome.tags_seen:>6} "
            f"{outcome.tags_written:>14} {outcome.tags_derived:>7}  {outcome.stop_reason:<12} "
            f"{outcome.elapsed_seconds:>7.1f}"
        )
    for outcome in outcomes:
        if outcome.stop_reason == STOP_ERROR:
            lines.append(f"  {outcome.repository}: {outcome.error}")
    return "\n".join(lines)


def _target_repositories(args: argparse.Namespace) -> Sequence[str]:
    return OFFICIAL_REPOSITORIES if args.all else (args.repository,)


def _run_each(repositories: Sequence[str], run_one: Callable[[str], str]) -> int:
    """repository마다 run_one을 부르고 그 결과 줄을 출력한다. 실패가 있으면 1을 돌려준다.

    collect는 이 loop를 쓰지 않는다. 실패 격리와 실행 기록을 collect_all이 맡고,
    결과를 표 하나로 모아 출력한다.
    """
    failed = 0
    for repository in repositories:
        try:
            print(run_one(repository))
        except Exception as exc:
            # 한 repository의 실패가 나머지를 막지 않는다.
            failed += 1
            print(f"{repository:<16} failed: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


def cmd_collect(args: argparse.Namespace) -> None:
    repositories = _target_repositories(args)
    engine = make_engine(args.database_url)
    with httpx2.Client(timeout=30.0) as http:
        code = run_collect(engine, HubClient(http), repositories, args.max_pages)
    if code:
        raise SystemExit(code)


def run_derive(engine: Engine, repositories: Sequence[str]) -> int:
    """이미 수집된 태그에 파생 값을 다시 채운다. Docker Hub를 부르지 않는다."""

    def derive_one(repository: str) -> str:
        with session_scope(engine) as session:
            return _format_derive_line(derive_repository(session, repository))

    return _run_each(repositories, derive_one)


def _format_derive_line(outcome: DeriveOutcome) -> str:
    filled = outcome.with_distribution / outcome.total if outcome.total else 0.0
    return (
        f"{outcome.repository:<16} tags {outcome.total:>5}  changed {outcome.changed:>5}  "
        f"conflicts {outcome.conflict_fields} fields / {outcome.conflict_tags} tags  "
        f"distribution {filled:.1%}"
    )


def cmd_derive(args: argparse.Namespace) -> None:
    code = run_derive(make_engine(args.database_url), _target_repositories(args))
    if code:
        raise SystemExit(code)


def run_index(
    engine: Engine,
    client: HubClient,
    embedder: Embedder,
    repositories: Sequence[str],
    fetch_readme: Callable[[str], str],
) -> int:
    """README 본문은 fetch_readme(원본)에서 받는다. Hub 본문은 25,000자에서 잘린다.

    원본을 받지 못하면 잘린 Hub 본문으로 대신하지 않고 그 repository를 실패로 센다.
    이전 색인은 그대로 남는다.
    """

    def index_one(repository: str) -> str:
        repo_row = parse_repository(client.fetch_repository(repository))
        readme = fetch_readme(repository)
        now = datetime.now(UTC)
        with session_scope(engine) as session:
            upsert_repository(session, repo_row, now)
            created = index_readme(
                session, repository, readme, docs.docs_page_url(repository), embedder, now
            )
        return f"{repository:<16} indexed {created} chunks"

    return _run_each(repositories, index_one)


def cmd_index(args: argparse.Namespace) -> None:
    repositories = _target_repositories(args)
    engine = make_engine(args.database_url)
    embedder = get_embedder(args.embedder)
    with httpx2.Client(timeout=30.0) as http:
        code = run_index(
            engine,
            HubClient(http),
            embedder,
            repositories,
            lambda repository: docs.fetch_readme(http, repository),
        )
    if code:
        raise SystemExit(code)


def cmd_search(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    embedder = get_embedder(args.embedder)
    with session_scope(engine) as session:
        for chunk, distance in search_chunks(session, embedder, args.question, limit=args.limit):
            # 여러 repository의 같은 제목 섹션이 함께 나오므로 repository를 같이 적는다.
            print(f"[{distance:.4f}] {chunk.document.repository} — {chunk.document.section_title}")
            print(f"    {chunk.content[:160].replace(chr(10), ' ')}")


def indexed_repositories(session: Session) -> set[str]:
    """임베딩이 있는 문서 청크를 하나 이상 가진 repository 이름을 반환한다.

    수집과 색인은 별도 단계다. 필요한 repository가 색인되지 않은 문항은
    검색 품질을 평가할 수 없으므로, 0점으로 채점하지 않고 미측정으로 분류한다.
    청크의 존재만 확인하며 색인이 완전하거나 최신인지는 검사하지 않는다.
    """
    return set(
        session.execute(
            select(Repository.name)
            .join(Document, Document.repository == Repository.name)
            .join(DocumentChunk, DocumentChunk.document_id == Document.id)
            .where(DocumentChunk.embedding.is_not(None))
            .distinct()
        ).scalars()
    )


def accepted_digests(session: Session, accept: list[str]) -> frozenset[str]:
    """문항 accept 태그들의 digest를 모은다. 채점이 이름만 다른 같은 이미지를 알아보게 한다.

    측정 시점 DB 기준이다. 수집되지 않았거나 digest가 없는 태그는 빠진다.
    """
    pairs = [parts for image in accept if (parts := split_image(image)) is not None]
    if not pairs:
        return frozenset()
    return frozenset(
        session.execute(
            select(ImageTag.manifest_digest).where(
                tuple_(ImageTag.repository, ImageTag.tag).in_(pairs),
                ImageTag.manifest_digest.is_not(None),
            )
        ).scalars()
    )


def timed_recommendation(
    open_session: SessionFactory,
    embedder: Embedder,
    provider: LLMProvider,
    question: str,
    call_log: list[LLMCall] | None = None,
) -> tuple[RecommendResponse, RunTrace]:
    """추천 경로를 한 번 돌리며 전체·단계별 시간과 LLM #2의 입력을 남긴다.

    임베더와 LLM 공급자를 문항마다 새 프록시로 감싼다. API와 추천 코드는 모른다.
    call_log는 provider가 호출 기록을 쌓는 목록이다. 실행 전체가 함께 쓰므로 이 문항에서
    늘어난 부분만 가져간다.
    """
    log = call_log if call_log is not None else []
    already = len(log)
    timed_embedder = Timed(embedder, ["embed"])
    timed_provider = Timed(provider, ["plan", "recommend"])
    start = time.perf_counter()
    response = recommend_for_question(open_session, timed_embedder, timed_provider, question)
    total = time.perf_counter() - start
    advise = timed_provider.calls.get("recommend")
    return response, RunTrace(
        seconds_total=total,
        seconds_embedding=timed_embedder.seconds.get("embed"),
        seconds_plan=timed_provider.seconds.get("plan"),
        seconds_advise=timed_provider.seconds.get("recommend"),
        # recommend(system, prompt)의 두 번째 인자가 LLM #2에 보낸 프롬프트다.
        advise_prompt=advise[0][1] if advise else None,
        llm_calls=[asdict(call) for call in log[already:]],
    )


def cmd_eval(args: argparse.Namespace) -> None:
    # eval은 dev 의존성인 PyYAML을 쓴다. 모듈 최상단에서 가져오면 PyYAML이 없는
    # 환경에서 collect·index 같은 다른 명령까지 import 단계에서 실패한다.
    from whatfrom.eval.goldenset import load_goldenset

    goldenset = load_goldenset(Path(args.goldenset))
    open_session = session_factory(make_engine(args.database_url))
    embedder = get_embedder(args.embedder)
    measured, skipped = _eval_cases(goldenset, args.tags, open_session)
    mode = _eval_mode(args)
    # 결과 파일이 맞지 않으면 모델을 부르기 전에 멈춘다.
    fixed_plans = (
        _load_fixed_plans(Path(args.plans), Path(args.goldenset), measured) if args.plans else None
    )
    # provider가 호출마다 기록을 쌓는다. 문항별로 나누는 일은 timed_recommendation이 한다.
    call_log: list[LLMCall] = []
    provider = (
        get_provider(args.llm_provider, config=settings, on_call=call_log.append)
        if mode.calls_llm
        else None
    )
    started_at = datetime.now(UTC)

    results_dir = Path(args.results_dir)
    # 결과 디렉터리를 생성할 수 없는 오류는 외부 모델 호출 전에 발견한다.
    # 디렉터리가 이미 존재할 때의 파일 쓰기 권한까지 확인하는 것은 아니다.
    results_dir.mkdir(parents=True, exist_ok=True)

    scores = []
    for index, case in enumerate(measured, start=1):
        print(f"[{index}/{len(measured)}] {case.id}", flush=True)
        if mode.name == "plan-only":
            assert provider is not None
            scores.append(_score_plan_case(case, open_session, provider, call_log))
        else:
            case_provider = (
                provider
                if fixed_plans is None or provider is None
                else FixedPlanProvider(fixed_plans[case.id], provider)
            )
            scores.append(_score_case(case, open_session, embedder, case_provider, call_log))

    meta = _eval_meta(args, mode, goldenset, started_at)
    out = results_dir / f"{started_at.strftime('%Y-%m-%dT%H-%M-%S')}.json"
    _report_eval(mode, goldenset, measured, skipped, scores, meta, out)


@dataclass(frozen=True)
class EvalMode:
    """평가 모드마다 어느 단계를 부르는지. 모드에 따른 분기는 모두 이 값에서 나온다."""

    name: str
    calls_plan: bool
    calls_recommend: bool
    # 검색을 해서 후보가 있는가. 무작위 선택 대조군을 계산할 수 있다.
    has_candidates: bool

    @property
    def calls_llm(self) -> bool:
        return self.calls_plan or self.calls_recommend


EVAL_MODES = {
    mode.name: mode
    for mode in (
        EvalMode("full", calls_plan=True, calls_recommend=True, has_candidates=True),
        EvalMode("retrieval-only", calls_plan=False, calls_recommend=False, has_candidates=True),
        EvalMode("plan-only", calls_plan=True, calls_recommend=False, has_candidates=False),
        EvalMode("fixed-plans", calls_plan=False, calls_recommend=True, has_candidates=True),
    )
}


def _eval_mode(args: argparse.Namespace) -> EvalMode:
    if args.retrieval_only:
        return EVAL_MODES["retrieval-only"]
    if args.plan_only:
        return EVAL_MODES["plan-only"]
    if args.plans:
        return EVAL_MODES["fixed-plans"]
    return EVAL_MODES["full"]


class FixedPlanProvider:
    """조건 추출 단계 대신 이전 실행의 검색 조건을 돌려준다(--plans). 추천 단계는 그대로 부른다.

    기록된 검색 조건이 None이면 그때 추출이 실패한 것이다. 같은 입력을 주기 위해 이번에도
    실패로 다룬다.
    """

    def __init__(self, plan: dict | None, inner: LLMProvider) -> None:
        self._plan = plan
        self._inner = inner

    def plan(self, system: str, prompt: str) -> SearchPlan:
        if self._plan is None:
            raise RemoteCallError("고정한 결과에서 검색 조건 추출이 실패한 문항이다")
        return SearchPlan.model_validate(self._plan)

    def recommend(self, system: str, prompt: str) -> Recommendation:
        return self._inner.recommend(system, prompt)


def _load_fixed_plans(path: Path, goldenset: Path, measured: list) -> dict[str, dict | None]:
    """--plans 결과 파일에서 문항별 검색 조건을 읽는다. 비교할 수 없는 파일이면 멈춘다."""
    document = json.loads(path.read_text(encoding="utf-8"))
    meta = document.get("meta", {})
    if meta.get("mode") != "full":
        raise SystemExit(f"--plans에는 전체 모드(full) 결과가 필요하다: {path}")
    if meta.get("goldenset_sha256") != hashlib.sha256(goldenset.read_bytes()).hexdigest():
        raise SystemExit(f"--plans 결과의 golden set이 지금 golden set과 다르다: {path}")
    plans = {case["case_id"]: case.get("plan") for case in document.get("cases", [])}
    missing = [case.id for case in measured if case.id not in plans]
    if missing:
        raise SystemExit(f"--plans 결과에 없는 문항: {', '.join(missing)}")
    return plans


def _eval_cases(
    goldenset: "GoldenSet",
    tags: str | None,
    open_session: SessionFactory,
) -> tuple[list, list]:
    """--tags로 거른 문항을, 필요한 repository가 색인된 것과 아닌 것으로 나눈다."""
    from whatfrom.eval.report import Skipped

    wanted = {tag for tag in (tags or "").split(",") if tag}
    # --tags는 OR다. 지정한 태그 중 하나라도 가진 문항을 남긴다.
    cases = [c for c in goldenset.cases if not wanted or wanted & set(c.tags)]
    with open_session() as session:
        available = indexed_repositories(session)

    measured = []
    skipped: list[Skipped] = []
    for case in cases:
        missing = [r for r in case.requires_repositories if r not in available]
        if missing:
            skipped.append(Skipped(case_id=case.id, missing=missing))
        else:
            measured.append(case)
    return measured, skipped


def _score_case(
    case: "GoldenCase",
    open_session: SessionFactory,
    embedder: Embedder,
    provider: LLMProvider | None,
    call_log: list[LLMCall],
):
    """문항 하나를 채점한다. provider가 None이면 검색 지표만 잰다."""
    from whatfrom.eval.scoring import score_full, score_retrieval

    # 임베딩 오류는 검색 품질의 0점으로 집계하지 않고 실행을 중단시킨다.
    vector = embedder.embed([case.question])[0]
    with open_session() as session:
        hits = search_chunks_by_vector(session, vector, limit=5)
        # 제목만 넘기면 다른 repository의 같은 이름 섹션도 히트가 된다. repository를
        # 함께 넘겨 채점이 문항이 묻는 repository의 문서만 보게 한다.
        sections = [(chunk.document.repository, chunk.document.section_title) for chunk, _ in hits]

    if provider is None:
        with open_session() as session:
            candidates = search_candidates_by_vector(session, vector)
            digests = accepted_digests(session, case.accept)
        return score_retrieval(case, candidates, sections, digests)

    # Hit@5는 두 모드 모두 후보 선택 전의 상위 5개 청크로 계산한다.
    # 추천 응답의 evidence에는 후보 선택에서 제외된 문서가 없을 수 있다.
    # 추천 경로도 별도로 실행하므로 전체 모드는 질문을 두 번 임베딩한다.
    response, trace = timed_recommendation(
        open_session, embedder, provider, case.question, call_log
    )

    exists: bool | None = None
    if response.recommendation is not None:
        with open_session() as session:
            exists = image_exists(session, response.recommendation.image)
    with open_session() as session:
        digests = accepted_digests(session, case.accept)
    return replace(score_full(case, response, sections, exists, digests), trace=trace)


def _eval_meta(
    args: argparse.Namespace, mode: EvalMode, goldenset: "GoldenSet", started_at: datetime
) -> dict:
    """결과 JSON의 실행 meta. 서로 다른 실행의 점수를 비교할 수 있는지 판단하는 근거다."""
    # 부르지 않은 단계는 None이다.
    stages = {"plan": mode.calls_plan, "recommend": mode.calls_recommend}
    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": mode.name,
        "embedder": args.embedder,
        # get_embedder/get_provider와 같은 기준(이름이 "fake"인지)으로 판단한다.
        # fake로 돌린 실행에 실제 모델 이름을 붙이면 서로 다른 모델의 점수를
        # 같은 것처럼 비교하게 된다.
        "embedding_model": None if args.embedder == "fake" else settings.embedding_model,
        # 단계마다 실제로 쓴 base URL, 모델, 덧붙일 JSON. API 키는 남기지 않는다.
        "llm_stages": (
            {stage: _stage_meta(stage) if used else None for stage, used in stages.items()}
            if mode.calls_llm and args.llm_provider != "fake"
            else None
        ),
        "llm_provider": args.llm_provider if mode.calls_llm else None,
        "tags": args.tags or None,
        # 검색 조건을 고정한 결과 파일. 두 실행이 같은 입력을 받았는지 확인하는 근거다.
        "plans_from": Path(args.plans).name if args.plans else None,
        "goldenset_version": goldenset.version,
        # 버전 번호를 유지한 채 라벨을 수정할 수 있으므로 파일 해시도 기록한다.
        # 해시가 다르면 두 실행이 사용한 golden set 내용이 달랐다는 뜻이다.
        "goldenset_sha256": hashlib.sha256(Path(args.goldenset).read_bytes()).hexdigest(),
        # golden set이 인용한 출처를 마지막으로 확인한 날. latest·LTS·최신 패치 주장은
        # 시간이 지나면 낡는다. 언제 기준의 라벨인지 결과에 남긴다.
        "goldenset_verified_on": (
            goldenset.verified_on.isoformat() if goldenset.verified_on is not None else None
        ),
    }


def _stage_meta(stage: str) -> dict:
    resolved = stage_llm(settings, stage)
    return {
        "base_url": resolved.base_url,
        "model": resolved.model,
        "extra_body": resolved.extra_body,
    }


def _score_plan_case(
    case: "GoldenCase",
    open_session: SessionFactory,
    provider: LLMProvider,
    call_log: list[LLMCall],
):
    """조건 추출 단계만 돌려 채점한다(--plan-only). 임베딩과 추천 단계는 부르지 않는다.

    repository 목록과 정규화는 추천 경로와 같은 함수를 쓴다. 그래야 전체 모드의 추출
    지표와 비교할 수 있다.
    """
    from whatfrom.eval.scoring import score_plan

    with open_session() as session:
        repositories = repository_names(session)
    timed = Timed(provider, ["plan"])
    already = len(call_log)
    notes: list[str] = []
    start = time.perf_counter()
    try:
        plan = extract_plan(timed, case.question, repositories)
    except RemoteCallError as exc:
        plan = None
        notes.append(f"검색 조건 추출에 실패했습니다: {exc}")
    total = time.perf_counter() - start
    trace = RunTrace(
        seconds_total=total,
        seconds_embedding=None,
        seconds_plan=timed.seconds.get("plan"),
        seconds_advise=None,
        advise_prompt=None,
        llm_calls=[asdict(call) for call in call_log[already:]],
    )
    return replace(score_plan(case, plan, notes), trace=trace)


def _report_eval(
    mode: EvalMode,
    goldenset: "GoldenSet",
    measured: list,
    skipped: list,
    scores: list,
    meta: dict,
    out: Path,
) -> None:
    """지표를 집계해 요약을 출력하고 결과 JSON을 저장한다."""
    from whatfrom.eval.report import (
        aggregate_full,
        aggregate_plan,
        aggregate_recommendation,
        aggregate_retrieval,
        constant_baseline,
        random_baseline,
        render_summary,
        result_document,
    )

    # 부른 단계의 지표만 낸다. 검색 조건을 고정한 실행의 추출 지표는 이번 실행의 것이 아니다.
    if mode.calls_recommend:
        metrics = aggregate_full(scores) if mode.calls_plan else aggregate_recommendation(scores)
    elif mode.has_candidates:
        metrics = aggregate_retrieval(scores)
    else:
        metrics = aggregate_plan(scores)
    # 추천 정확도와 비교할 고정답 기준선을 계산한다.
    # 측정 문항의 accept 목록만 사용하며 추가 DB 조회나 모델 호출은 없다.
    baseline = constant_baseline(measured) if mode.calls_recommend else None
    # 후보 기록만으로 계산하므로 후보가 있는 모드만 구한다.
    random = random_baseline(scores) if mode.has_candidates else None

    print()
    # 실패 목록은 추천 결과를 보여 준다. 추천이 없는 모드에는 놓일 자리가 없다.
    failures = scores if mode.calls_recommend else []
    print(
        render_summary(
            metrics, failures, measured, skipped, len(goldenset.cases), meta, baseline, random
        )
    )

    out.write_text(
        json.dumps(
            result_document(metrics, scores, skipped, meta, baseline, random),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{out} 저장")


def question_order(count: int, blocks: int, seed: int) -> list[int]:
    """질문 인덱스의 순열을 blocks개 이어 붙인다. 같은 시드면 같은 순서다.

    부하 시험은 이 순서표에서 질문을 고른다. 무작위로 고르면 실행 속도에 따라 두 실행의
    질문 구성이 달라지고, 질문마다 LLM 시간이 달라 그 차이가 결과에 섞인다.
    """
    rng = random.Random(seed)
    order: list[int] = []
    for _ in range(blocks):
        block = list(range(count))
        rng.shuffle(block)
        order += block
    return order


def question_file(goldenset: "GoldenSet", seed: int = 0, blocks: int = 12) -> dict:
    """k6와 모의 LLM 서버가 읽는 질문 파일. k6는 YAML을 읽지 못한다.

    blocks=12면 480개다. k6는 구간마다 오프셋(평소 0, 스파이크 40, 회복 420)에서 시작해
    예정 요청 수(36, 360, 60)만큼 쓴다. 회복 구간은 계획보다 한 번 더 돌 수 있어, 61번째
    반복(420+60=480)이 순서표 끝을 넘어 처음(order[0])으로 돌아갈 수 있다. 나머지 연산이라
    결정적이므로 비교에 영향은 없다.
    """
    questions = [{"id": case.id, "question": case.question} for case in goldenset.cases]
    return {
        "seed": seed,
        "questions": questions,
        "order": question_order(len(questions), blocks, seed),
    }


def cmd_load_questions(args: argparse.Namespace) -> None:
    # eval과 같은 이유로 PyYAML을 쓰는 모듈은 여기서 가져온다.
    from whatfrom.eval.goldenset import load_goldenset

    document = question_file(load_goldenset(Path(args.goldenset)), seed=args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{out} 저장 ({len(document['questions'])}문항, 순서 {len(document['order'])}개)")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 한다")
    return parsed


def _add_repository_target(parser: argparse.ArgumentParser, all_help: str) -> None:
    """repository 하나 또는 --all 중 하나를 반드시 받는다."""
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("repository", nargs="?")
    target.add_argument("--all", action="store_true", help=all_help)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatfrom")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables")
    p_init.add_argument("--database-url", default=settings.database_url)
    p_init.set_defaults(func=cmd_init_db)

    p_collect = sub.add_parser("collect", help="collect tags from Docker Hub")
    _add_repository_target(p_collect, all_help="스펙의 공식 이미지 10개를 모두 수집")
    # 익명 요청은 repository당 10페이지(1,000개)까지만 닿는다.
    p_collect.add_argument("--max-pages", type=_positive_int, default=None)
    p_collect.add_argument("--database-url", default=settings.database_url)
    p_collect.set_defaults(func=cmd_collect)

    p_derive = sub.add_parser("derive", help="fill derived tag columns from collected tags")
    _add_repository_target(p_derive, all_help="공식 이미지 10개를 모두 채움")
    p_derive.add_argument("--database-url", default=settings.database_url)
    p_derive.set_defaults(func=cmd_derive)

    p_index = sub.add_parser("index", help="fetch README, chunk it, embed it")
    _add_repository_target(p_index, all_help="공식 이미지 10개를 모두 색인")
    p_index.add_argument("--embedder", default=settings.embedder)
    p_index.add_argument("--database-url", default=settings.database_url)
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="similarity search over README chunks")
    p_search.add_argument("question")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.add_argument("--embedder", default=settings.embedder)
    p_search.add_argument("--database-url", default=settings.database_url)
    p_search.set_defaults(func=cmd_search)

    p_eval = sub.add_parser("eval", help="score the golden set")
    p_eval.add_argument("--goldenset", default="eval/goldenset.yaml")
    p_eval.add_argument("--results-dir", default="eval/results")
    p_eval.add_argument("--tags", default="", help="쉼표로 구분. 하나라도 가진 문항만 (OR)")
    modes = p_eval.add_mutually_exclusive_group()
    modes.add_argument("--retrieval-only", action="store_true", help="LLM 없이 검색 지표만")
    modes.add_argument("--plan-only", action="store_true", help="조건 추출 단계만 돌려 추출 지표만")
    modes.add_argument(
        "--plans",
        metavar="RESULT_JSON",
        help="이전 전체 평가 결과의 검색 조건을 고정하고 추천 단계만 바꿔 잰다",
    )
    p_eval.add_argument("--embedder", default=settings.embedder)
    p_eval.add_argument("--llm-provider", default=settings.llm_provider)
    p_eval.add_argument("--database-url", default=settings.database_url)
    p_eval.set_defaults(func=cmd_eval)

    p_load = sub.add_parser("load-questions", help="export load test questions and a fixed order")
    p_load.add_argument("--goldenset", default="eval/goldenset.yaml")
    p_load.add_argument("--out", default="load/questions.json")
    p_load.add_argument("--seed", type=int, default=0)
    p_load.set_defaults(func=cmd_load_questions)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
