# src/whatfrom/collect/sync.py
"""리포지토리 수집 흐름과 실행 기록.

매 실행 익명 요청이 닿는 범위(최근 1,000개)를 전부 받는다. 바뀐 태그만 다시
쓰는 판단은 store.upsert_tags가 한다.
"""

import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from whatfrom.collect.derive import derive_repository
from whatfrom.collect.hub import HubClient, OffsetLimitReached, parse_repository, parse_tag_page
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.db import session_scope
from whatfrom.core.models import CollectionRun

STOP_END = "end"
STOP_OFFSET_LIMIT = "offset_limit"
STOP_MAX_PAGES = "max_pages"
STOP_ERROR = "error"
STOP_INTERRUPTED = "interrupted"

# 스펙 §3의 공식 이미지. collect --all과 index --all이 같은 목록을 쓴다.
OFFICIAL_REPOSITORIES = (
    "python",
    "node",
    "eclipse-temurin",
    "golang",
    "nginx",
    "postgres",
    "redis",
    "debian",
    "ubuntu",
    "alpine",
)


@dataclass(frozen=True)
class CollectOutcome:
    repository: str
    stop_reason: str
    pages: int
    tags_seen: int
    tags_written: int
    elapsed_seconds: float
    error: str | None
    # 수집을 끝까지 마친 뒤 파생 값이 바뀐 태그 수. 실패한 수집은 파생을 돌리지 않아 0이다.
    tags_derived: int = 0


def collect_repository(
    engine: Engine,
    client: HubClient,
    repository: str,
    max_pages: int | None,
    now: datetime,
) -> CollectOutcome:
    """리포지토리 하나를 수집하고 collection_runs에 기록한다.

    실행 행은 첫 HTTP 요청 전에 커밋한다. 요청이 바로 실패해도 기록이 남는다.
    페이지마다 태그 저장과 카운터 갱신을 한 트랜잭션으로 커밋한다. 둘이 어긋나면
    기록과 실제 저장이 다른 말을 한다.

    네트워크 왕복은 트랜잭션 밖에서 일어난다. 한 트랜잭션에서 페이지를 계속 받으면
    Docker Hub가 느린 만큼 커넥션과 락을 붙잡는다.

    실패하면 기록하고 원래 예외를 다시 올린다. 기록 자체가 실패해도 실행 행의
    finished_at이 NULL로 남아 미완료로 보인다.

    스캔을 다 마친 뒤에도 파생 값 채우기(derive_repository)가 실패하면 실행은
    스캔 자신의 중단 사유가 아니라 error로 기록된다. 이미 커밋된 페이지는 그대로
    남으므로 derive 명령으로 나중에 다시 채울 수 있다.
    """
    run_id = _start_run(engine, repository)
    return _collect_run(engine, client, repository, max_pages, now, run_id)


def _collect_run(
    engine: Engine,
    client: HubClient,
    repository: str,
    max_pages: int | None,
    now: datetime,
    run_id: int,
) -> CollectOutcome:
    """이미 만든 실행 행 run_id에 기록하며 수집한다. now는 태그의 collected_at이다."""
    pages = seen = written = 0
    try:
        repo_row = parse_repository(client.fetch_repository(repository))
        with session_scope(engine) as session:
            upsert_repository(session, repo_row, now)

        stop_reason = STOP_END
        try:
            for payload in client.iter_tag_pages(repository, page_size=100):
                rows = parse_tag_page(payload)
                with session_scope(engine) as session:
                    page_written = upsert_tags(session, repository, rows, now)
                    _add_page_counts(session, run_id, len(rows), page_written)
                pages += 1
                seen += len(rows)
                written += page_written
                # next가 없으면 제한에 닿았어도 범위를 다 받은 것이다.
                if max_pages is not None and pages >= max_pages and payload.get("next"):
                    stop_reason = STOP_MAX_PAGES
                    break
        except OffsetLimitReached:
            stop_reason = STOP_OFFSET_LIMIT

        # 별칭은 다른 페이지의 태그에서 값을 물려받으므로 태그를 다 받은 뒤에 채운다.
        with session_scope(engine) as session:
            derived = derive_repository(session, repository).changed
    except Exception as exc:
        with suppress(Exception):
            _finish_run(engine, run_id, STOP_ERROR, None, f"{type(exc).__name__}: {exc}")
        raise
    except BaseException as exc:
        with suppress(Exception):
            _finish_run(engine, run_id, STOP_INTERRUPTED, None, type(exc).__name__)
        raise

    _finish_run(engine, run_id, stop_reason, datetime.now(UTC), None)
    return CollectOutcome(repository, stop_reason, pages, seen, written, 0.0, None, derived)


def collect_all(
    engine: Engine,
    client: HubClient,
    repositories: Sequence[str],
    max_pages: int | None,
    now: datetime,
) -> list[CollectOutcome]:
    """여러 리포지토리를 순서대로 수집한다.

    Exception은 리포지토리별로 격리한다. 한 리포지토리의 실패가 나머지를 막지 않는다.
    KeyboardInterrupt 같은 중단은 격리하지 않는다. 사용자가 멈추려 한 것이다.

    실패 결과는 이번에 만든 실행 행을 id로 읽는다. 리포지토리 이름으로 최신 행을
    찾으면 실행 행 생성 자체가 실패했을 때 이전 실행을 보고한다.
    """
    outcomes: list[CollectOutcome] = []
    for repository in repositories:
        started = time.monotonic()
        run_id: int | None = None
        try:
            run_id = _start_run(engine, repository)
            outcome = _collect_run(engine, client, repository, max_pages, now, run_id)
        except Exception as exc:
            outcome = _failed_outcome(engine, repository, run_id, exc)
        outcomes.append(replace(outcome, elapsed_seconds=time.monotonic() - started))
    return outcomes


def _start_run(engine: Engine, repository: str) -> int:
    # started_at은 실제 시작 시각이다. 배치가 공유하는 now를 쓰면 finished_at과 다른
    # 시계가 되어 둘의 차가 실행 시간이 아니다.
    with session_scope(engine) as session:
        run = CollectionRun(
            repository=repository,
            started_at=datetime.now(UTC),
            pages=0,
            tags_seen=0,
            tags_written=0,
        )
        session.add(run)
        session.flush()
        return run.id


def _add_page_counts(session: Session, run_id: int, seen: int, written: int) -> None:
    session.execute(
        update(CollectionRun)
        .where(CollectionRun.id == run_id)
        .values(
            pages=CollectionRun.pages + 1,
            tags_seen=CollectionRun.tags_seen + seen,
            tags_written=CollectionRun.tags_written + written,
        )
        .execution_options(synchronize_session=False)
    )


def _finish_run(
    engine: Engine,
    run_id: int,
    stop_reason: str,
    finished_at: datetime | None,
    error: str | None,
) -> None:
    with session_scope(engine) as session:
        session.execute(
            update(CollectionRun)
            .where(CollectionRun.id == run_id)
            .values(stop_reason=stop_reason, finished_at=finished_at, error=error)
            .execution_options(synchronize_session=False)
        )


def _failed_outcome(
    engine: Engine, repository: str, run_id: int | None, exc: Exception
) -> CollectOutcome:
    """실패한 실행의 결과. 예외를 올리지 않는다. 올리면 나머지 리포지토리가 멈춘다.

    실행 행이 없거나 읽지 못하면 잡은 예외로 결과를 만든다.
    """
    message = f"{type(exc).__name__}: {exc}"
    if run_id is not None:
        with suppress(Exception), session_scope(engine) as session:
            run = session.get(CollectionRun, run_id)
            if run is not None:
                return CollectOutcome(
                    repository=repository,
                    stop_reason=run.stop_reason or STOP_ERROR,
                    pages=run.pages,
                    tags_seen=run.tags_seen,
                    tags_written=run.tags_written,
                    elapsed_seconds=0.0,
                    error=run.error or message,
                )
    return CollectOutcome(repository, STOP_ERROR, 0, 0, 0, 0.0, message)
