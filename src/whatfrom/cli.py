# src/whatfrom/cli.py
import argparse
from datetime import UTC, datetime

import httpx2
from sqlalchemy import text

from whatfrom.collect.hub import HubClient, parse_repository, parse_tag_page
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.config import settings
from whatfrom.db import make_engine, session_scope
from whatfrom.models import Base


def cmd_init_db(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    print(f"initialized schema on {engine.url.database}")


def cmd_collect(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    now = datetime.now(UTC)
    total = 0
    with httpx2.Client(timeout=30.0) as http:
        client = HubClient(http)
        repo_row = parse_repository(client.fetch_repository(args.repository))
        with session_scope(engine) as session:
            upsert_repository(session, repo_row, now)
            for page in client.iter_tag_pages(
                args.repository, page_size=100, max_pages=args.max_pages
            ):
                total += upsert_tags(session, args.repository, parse_tag_page(page), now)
                print(f"  ... {total} tags")
    print(f"collected {total} tags for {args.repository}")


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

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
