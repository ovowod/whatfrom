# src/whatfrom/cli.py
import argparse

from sqlalchemy import text

from whatfrom.config import settings
from whatfrom.db import make_engine
from whatfrom.models import Base


def cmd_init_db(args: argparse.Namespace) -> None:
    engine = make_engine(args.database_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    print(f"initialized schema on {engine.url.database}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whatfrom")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="create tables")
    p_init.add_argument("--database-url", default=settings.database_url)
    p_init.set_defaults(func=cmd_init_db)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
