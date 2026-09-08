# src/whatfrom/cli.py
import argparse
from datetime import UTC, datetime

import httpx2
from sqlalchemy import Engine, inspect, text

from whatfrom.collect.hub import HubClient, parse_repository, parse_tag_page
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.config import settings
from whatfrom.core.db import make_engine, session_scope
from whatfrom.core.embed import get_embedder
from whatfrom.core.models import Base
from whatfrom.index.indexer import index_readme
from whatfrom.search.retrieval import search_chunks


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


def collect_repository(
    engine: Engine,
    client: HubClient,
    repository: str,
    max_pages: int | None,
    now: datetime,
) -> int:
    """리포 하나를 수집한다. 페이지마다 짧은 트랜잭션을 연다.

    네트워크 왕복은 트랜잭션 밖에서 일어나야 한다. 한 트랜잭션 안에서
    페이지를 계속 받으면 Docker Hub가 느린 만큼 Postgres 커넥션과 락을
    붙잡고 있게 된다 — 리포 10개를 전량 수집하는 F5에서는 수 분이 된다.

    페이지 단위로 커밋하므로 중간에 실패해도 그때까지 받은 것은 남는다.
    upsert가 멱등이라 재실행하면 이어서 채워진다.
    """
    repo_row = parse_repository(client.fetch_repository(repository))
    with session_scope(engine) as session:
        upsert_repository(session, repo_row, now)

    total = 0
    for page in client.iter_tag_pages(repository, page_size=100, max_pages=max_pages):
        rows = parse_tag_page(page)
        # 트랜잭션은 이 블록 안에서만 열린다. 다음 페이지 요청은 블록을 나온 뒤다.
        with session_scope(engine) as session:
            total += upsert_tags(session, repository, rows, now)
        print(f"  ... {total} tags")
    return total


def cmd_collect(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    with httpx2.Client(timeout=30.0) as http:
        total = collect_repository(
            engine, HubClient(http), args.repository, args.max_pages, datetime.now(UTC)
        )
    print(f"collected {total} tags for {args.repository}")


def cmd_index(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    now = datetime.now(UTC)
    embedder = get_embedder(args.embedder)
    with httpx2.Client(timeout=30.0) as http:
        repo_row = parse_repository(HubClient(http).fetch_repository(args.repository))
    with session_scope(engine) as session:
        upsert_repository(session, repo_row, now)
        created = index_readme(
            session, args.repository, repo_row.readme, repo_row.source_url, embedder, now
        )
    print(f"indexed {created} chunks for {args.repository}")


def cmd_search(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    embedder = get_embedder(args.embedder)
    with session_scope(engine) as session:
        for chunk, distance in search_chunks(session, embedder, args.question, limit=args.limit):
            print(f"[{distance:.4f}] {chunk.document.section_title}")
            print(f"    {chunk.content[:160].replace(chr(10), ' ')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatfrom")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables")
    p_init.add_argument("--database-url", default=settings.database_url)
    p_init.set_defaults(func=cmd_init_db)

    p_collect = sub.add_parser("collect", help="collect tags from Docker Hub")
    p_collect.add_argument("repository")
    p_collect.add_argument("--max-pages", type=int, default=3)
    p_collect.add_argument("--database-url", default=settings.database_url)
    p_collect.set_defaults(func=cmd_collect)

    p_index = sub.add_parser("index", help="fetch README, chunk it, embed it")
    p_index.add_argument("repository")
    p_index.add_argument("--embedder", default=settings.embedder)
    p_index.add_argument("--database-url", default=settings.database_url)
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="similarity search over README chunks")
    p_search.add_argument("question")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.add_argument("--embedder", default=settings.embedder)
    p_search.add_argument("--database-url", default=settings.database_url)
    p_search.set_defaults(func=cmd_search)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
