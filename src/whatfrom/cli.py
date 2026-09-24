# src/whatfrom/cli.py
import argparse
import hashlib
import json
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from sqlalchemy import Engine, inspect, select, text, tuple_
from sqlalchemy.orm import Session, sessionmaker

from whatfrom.api import recommend_for_question
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
from whatfrom.core.db import make_engine, session_scope
from whatfrom.core.embed import Embedder, get_embedder
from whatfrom.core.models import Base, Document, DocumentChunk, ImageTag, Repository
from whatfrom.index.indexer import index_readme
from whatfrom.recommend.llm import get_provider
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


def cmd_collect(args: argparse.Namespace) -> None:
    repositories = OFFICIAL_REPOSITORIES if args.all else (args.repository,)
    engine = make_engine(args.database_url)
    with httpx2.Client(timeout=30.0) as http:
        code = run_collect(engine, HubClient(http), repositories, args.max_pages)
    if code:
        raise SystemExit(code)


def run_derive(engine: Engine, repositories: Sequence[str]) -> int:
    """이미 수집된 태그에 파생 값을 다시 채운다. Docker Hub를 부르지 않는다."""
    failed = 0
    for repository in repositories:
        try:
            with session_scope(engine) as session:
                outcome = derive_repository(session, repository)
            print(_format_derive_line(outcome))
        except Exception as exc:
            # 한 리포지토리의 실패가 나머지를 막지 않는다.
            failed += 1
            print(f"{repository:<16} failed: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


def _format_derive_line(outcome: DeriveOutcome) -> str:
    filled = outcome.with_distribution / outcome.total if outcome.total else 0.0
    return (
        f"{outcome.repository:<16} tags {outcome.total:>5}  changed {outcome.changed:>5}  "
        f"conflicts {outcome.conflict_fields} fields / {outcome.conflict_tags} tags  "
        f"distribution {filled:.1%}"
    )


def cmd_derive(args: argparse.Namespace) -> None:
    repositories = OFFICIAL_REPOSITORIES if args.all else (args.repository,)
    code = run_derive(make_engine(args.database_url), repositories)
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

    원본을 받지 못하면 잘린 Hub 본문으로 대신하지 않고 그 리포를 실패로 센다.
    이전 색인은 그대로 남는다.
    """
    failed = 0
    for repository in repositories:
        try:
            repo_row = parse_repository(client.fetch_repository(repository))
            readme = fetch_readme(repository)
            now = datetime.now(UTC)
            with session_scope(engine) as session:
                upsert_repository(session, repo_row, now)
                created = index_readme(
                    session, repository, readme, docs.docs_page_url(repository), embedder, now
                )
            print(f"{repository:<16} indexed {created} chunks")
        except Exception as exc:
            # 한 리포지토리의 실패가 나머지 색인을 막지 않는다.
            failed += 1
            print(f"{repository:<16} failed: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


def cmd_index(args: argparse.Namespace) -> None:
    repositories = OFFICIAL_REPOSITORIES if args.all else (args.repository,)
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
            # 여러 리포지토리의 같은 제목 섹션이 함께 나오므로 리포지토리를 같이 적는다.
            print(f"[{distance:.4f}] {chunk.document.repository} — {chunk.document.section_title}")
            print(f"    {chunk.content[:160].replace(chr(10), ' ')}")


def _open_session_factory(engine: Engine):
    """recommend_for_question이 요구하는 세션 팩토리를 만든다.

    DB 조회 구간마다 세션을 열고 닫아, 임베딩·LLM 응답을 기다리는 동안
    DB 연결과 트랜잭션을 유지하지 않도록 한다.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def open_session() -> Generator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    return open_session


def indexed_repositories(session: Session) -> set[str]:
    """임베딩이 있는 문서 청크를 하나 이상 가진 리포지터리 이름을 반환한다.

    수집과 색인은 별도 단계다. 필요한 리포지터리가 색인되지 않은 문항은
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
    pairs = [tuple(image.split(":", 1)) for image in accept if ":" in image]
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


def cmd_eval(args: argparse.Namespace) -> None:
    # eval은 dev 의존성인 PyYAML을 쓴다. 모듈 최상단에서 가져오면 PyYAML이 없는
    # 환경에서 collect·index 같은 다른 명령까지 import 단계에서 실패한다.
    from whatfrom.eval.goldenset import load_goldenset
    from whatfrom.eval.report import (
        Skipped,
        aggregate_full,
        aggregate_retrieval,
        constant_baseline,
        random_baseline,
        render_summary,
        result_document,
    )
    from whatfrom.eval.scoring import score_full, score_retrieval

    goldenset = load_goldenset(Path(args.goldenset))
    wanted = {tag for tag in (args.tags or "").split(",") if tag}
    # --tags는 OR다. 지정한 태그 중 하나라도 가진 문항을 남긴다.
    cases = [c for c in goldenset.cases if not wanted or wanted & set(c.tags)]

    engine = make_engine(args.database_url)
    open_session = _open_session_factory(engine)
    embedder = get_embedder(args.embedder)

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

    provider = None if args.retrieval_only else get_provider(args.llm_provider)
    started_at = datetime.now(UTC)
    scores: list = []

    results_dir = Path(args.results_dir)
    # 결과 디렉터리를 생성할 수 없는 오류는 외부 모델 호출 전에 발견한다.
    # 디렉터리가 이미 존재할 때의 파일 쓰기 권한까지 확인하는 것은 아니다.
    results_dir.mkdir(parents=True, exist_ok=True)

    for index, case in enumerate(measured, start=1):
        print(f"[{index}/{len(measured)}] {case.id}", flush=True)

        # 임베딩 오류는 검색 품질의 0점으로 집계하지 않고 실행을 중단시킨다.
        vector = embedder.embed([case.question])[0]
        with open_session() as session:
            hits = search_chunks_by_vector(session, vector, limit=5)
            # 제목만 넘기면 다른 리포의 같은 이름 섹션도 히트가 된다. 리포를
            # 함께 넘겨 채점이 문항이 묻는 리포의 문서만 보게 한다.
            sections = [
                (chunk.document.repository, chunk.document.section_title) for chunk, _ in hits
            ]

        if args.retrieval_only:
            with open_session() as session:
                candidates = search_candidates_by_vector(session, vector)
                digests = accepted_digests(session, case.accept)
            scores.append(score_retrieval(case, candidates, sections, digests))
            continue

        # Hit@5는 두 모드 모두 후보 선택 전의 상위 5개 청크로 계산한다.
        # 추천 응답의 evidence에는 후보 선택에서 제외된 문서가 없을 수 있다.
        # 추천 경로도 별도로 실행하므로 전체 모드는 질문을 두 번 임베딩한다.
        response = recommend_for_question(open_session, embedder, provider, case.question)

        exists: bool | None = None
        if response.recommendation is not None:
            repository, _, tag = response.recommendation.image.partition(":")
            with open_session() as session:
                exists = (
                    session.execute(
                        select(ImageTag.id).where(
                            ImageTag.repository == repository, ImageTag.tag == tag
                        )
                    ).scalar_one_or_none()
                    is not None
                )
        with open_session() as session:
            digests = accepted_digests(session, case.accept)
        scores.append(score_full(case, response, sections, exists, digests))

    meta = {
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "retrieval-only" if args.retrieval_only else "full",
        "embedder": args.embedder,
        # get_embedder/get_provider와 같은 기준(이름이 "fake"인지)으로 판단한다.
        # fake로 돌린 실행에 실제 모델 이름을 붙이면 서로 다른 모델의 점수를
        # 같은 것처럼 비교하게 된다.
        "embedding_model": None if args.embedder == "fake" else settings.embedding_model,
        "llm_model": (
            None if args.retrieval_only or args.llm_provider == "fake" else settings.llm_model
        ),
        "llm_provider": None if args.retrieval_only else args.llm_provider,
        "tags": args.tags or None,
        "goldenset_version": goldenset.version,
        # 버전 번호를 유지한 채 라벨을 수정할 수 있으므로 파일 해시도 기록한다.
        # 해시가 다르면 두 실행이 사용한 골든셋 내용이 달랐다는 뜻이다.
        "goldenset_sha256": hashlib.sha256(Path(args.goldenset).read_bytes()).hexdigest(),
        # 골든셋이 인용한 출처를 마지막으로 확인한 날. latest·LTS·최신 패치 주장은
        # 시간이 지나면 낡는다. 언제 기준의 라벨인지 결과에 남긴다.
        "goldenset_verified_on": (
            goldenset.verified_on.isoformat() if goldenset.verified_on is not None else None
        ),
    }
    metrics = aggregate_retrieval(scores) if args.retrieval_only else aggregate_full(scores)
    # 전체 모드의 추천 정확도와 비교할 고정답 기준선을 계산한다.
    # 측정 문항의 accept 목록만 사용하며 추가 DB 조회나 모델 호출은 없다.
    baseline = None if args.retrieval_only else constant_baseline(measured)
    # 후보 기록만으로 계산하므로 두 모드 모두 구한다.
    random = random_baseline(scores)

    print()
    failures = [] if args.retrieval_only else scores
    print(
        render_summary(
            metrics, failures, measured, skipped, len(goldenset.cases), meta, baseline, random
        )
    )

    out = results_dir / f"{started_at.strftime('%Y-%m-%dT%H-%M-%S')}.json"
    out.write_text(
        json.dumps(
            result_document(metrics, scores, skipped, meta, baseline, random),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{out} 저장")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("1 이상이어야 한다")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatfrom")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables")
    p_init.add_argument("--database-url", default=settings.database_url)
    p_init.set_defaults(func=cmd_init_db)

    p_collect = sub.add_parser("collect", help="collect tags from Docker Hub")
    collect_target = p_collect.add_mutually_exclusive_group(required=True)
    collect_target.add_argument("repository", nargs="?")
    collect_target.add_argument(
        "--all", action="store_true", help="스펙의 공식 이미지 10개를 모두 수집"
    )
    # 익명 요청은 리포지토리당 10페이지(1,000개)까지만 닿는다.
    p_collect.add_argument("--max-pages", type=_positive_int, default=None)
    p_collect.add_argument("--database-url", default=settings.database_url)
    p_collect.set_defaults(func=cmd_collect)

    p_derive = sub.add_parser("derive", help="fill derived tag columns from collected tags")
    derive_target = p_derive.add_mutually_exclusive_group(required=True)
    derive_target.add_argument("repository", nargs="?")
    derive_target.add_argument("--all", action="store_true", help="공식 이미지 10개를 모두 채움")
    p_derive.add_argument("--database-url", default=settings.database_url)
    p_derive.set_defaults(func=cmd_derive)

    p_index = sub.add_parser("index", help="fetch README, chunk it, embed it")
    index_target = p_index.add_mutually_exclusive_group(required=True)
    index_target.add_argument("repository", nargs="?")
    index_target.add_argument("--all", action="store_true", help="공식 이미지 10개를 모두 색인")
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
    p_eval.add_argument("--retrieval-only", action="store_true", help="LLM 없이 검색 지표만")
    p_eval.add_argument("--embedder", default=settings.embedder)
    p_eval.add_argument("--llm-provider", default=settings.llm_provider)
    p_eval.add_argument("--database-url", default=settings.database_url)
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
