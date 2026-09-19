# tests/test_cli_collect.py
"""collect·index 명령의 대상 선택과 실패 격리."""

import httpx2
import pytest
from sqlalchemy import delete, select

from whatfrom import cli
from whatfrom.cli import _format_collect_summary, build_parser, run_collect, run_index
from whatfrom.collect.hub import HubClient
from whatfrom.collect.sync import OFFICIAL_REPOSITORIES, CollectOutcome
from whatfrom.core.db import session_scope
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.models import (
    CollectionRun,
    Document,
    DocumentChunk,
    ImageTag,
    ImageVariant,
    Repository,
)


def hub(routes: dict[str, httpx2.Response]) -> HubClient:
    """URL 경로(쿼리 문자열 제외)로 응답을 정한다. 없는 경로는 404다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return routes.get(request.url.path, httpx2.Response(404))

    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    return HubClient(http, sleep=lambda _seconds: None)


def repository_payload(name: str) -> httpx2.Response:
    readme = "# Image Variants\n\nslim and alpine.\n"
    return httpx2.Response(
        200, json={"name": name, "namespace": "library", "full_description": readme}
    )


def readmes(texts: dict[str, str]):
    """README 원본 받기를 흉내 낸다. 없는 리포는 GitHub 404처럼 실패한다."""

    def fetch(repository: str) -> str:
        if repository not in texts:
            raise httpx2.HTTPStatusError(
                "404", request=httpx2.Request("GET", repository), response=httpx2.Response(404)
            )
        return texts[repository]

    return fetch


@pytest.fixture
def cleanup(engine):
    names: list[str] = []
    yield names
    with session_scope(engine) as session:
        for name in names:
            tag_ids = select(ImageTag.id).where(ImageTag.repository == name)
            doc_ids = select(Document.id).where(Document.repository == name)
            session.execute(delete(ImageVariant).where(ImageVariant.tag_id.in_(tag_ids)))
            session.execute(delete(ImageTag).where(ImageTag.repository == name))
            session.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(doc_ids)))
            session.execute(delete(Document).where(Document.repository == name))
            session.execute(delete(Repository).where(Repository.name == name))
            session.execute(delete(CollectionRun).where(CollectionRun.repository == name))


def test_collect_requires_exactly_one_target():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "python", "--all"])
    with pytest.raises(SystemExit):
        parser.parse_args(["collect"])


def test_index_requires_exactly_one_target():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["index", "python", "--all"])
    with pytest.raises(SystemExit):
        parser.parse_args(["index"])


def test_run_collect_keeps_going_and_reports_failure(engine, cleanup):
    cleanup.extend(["cli-bad", "cli-ok"])
    client = hub(
        {
            "/v2/repositories/library/cli-ok/tags": httpx2.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {
                            "name": "a",
                            "digest": "sha256:i",
                            "tag_last_pushed": "2026-09-01T00:00:00Z",
                            "images": [
                                {"os": "linux", "architecture": "amd64", "digest": "d", "size": 1}
                            ],
                        }
                    ],
                },
            ),
            "/v2/repositories/library/cli-ok/": repository_payload("cli-ok"),
        }
    )

    code = run_collect(engine, client, ["cli-bad", "cli-ok"], None)

    assert code == 1
    with session_scope(engine) as session:
        tags = session.execute(select(ImageTag.tag).where(ImageTag.repository == "cli-ok"))
        assert list(tags.scalars()) == ["a"]


def test_run_index_keeps_going_and_reports_failure(engine, cleanup):
    cleanup.extend(["cli-index-bad", "cli-index-ok"])
    client = hub({"/v2/repositories/library/cli-index-ok/": repository_payload("cli-index-ok")})

    code = run_index(
        engine,
        client,
        FakeEmbedder(),
        ["cli-index-bad", "cli-index-ok"],
        readmes({"cli-index-bad": "# A\n\na.\n", "cli-index-ok": "# A\n\na.\n"}),
    )

    assert code == 1
    with session_scope(engine) as session:
        documents = session.execute(
            select(Document.id).where(Document.repository == "cli-index-ok")
        )
        assert list(documents.scalars())


def test_run_index_indexes_the_docs_readme_not_the_hub_description(engine, cleanup):
    """Hub 본문은 25,000자에서 잘린다. 본문은 원본에서 받고 출처도 원본 문서다."""
    cleanup.append("cli-index-docs")
    client = hub({"/v2/repositories/library/cli-index-docs/": repository_payload("cli-index-docs")})
    full = "# Quick reference\n\nlinks.\n\n# License\n\nMIT.\n"

    code = run_index(
        engine, client, FakeEmbedder(), ["cli-index-docs"], readmes({"cli-index-docs": full})
    )

    assert code == 0
    with session_scope(engine) as session:
        rows = session.execute(
            select(Document.section_title, Document.source_url).where(
                Document.repository == "cli-index-docs"
            )
        ).all()
    assert {title for title, _ in rows} == {"Quick reference", "License"}
    assert {url for _, url in rows} == {
        "https://github.com/docker-library/docs/blob/master/cli-index-docs/README.md"
    }


def test_run_index_fails_a_repository_whose_readme_cannot_be_fetched(engine, cleanup):
    """잘린 Hub 본문으로 대신하지 않는다. 그 리포만 실패로 세고 이전 색인은 남긴다."""
    cleanup.extend(["cli-index-kept", "cli-index-next"])
    client = hub(
        {
            "/v2/repositories/library/cli-index-kept/": repository_payload("cli-index-kept"),
            "/v2/repositories/library/cli-index-next/": repository_payload("cli-index-next"),
        }
    )
    run_index(
        engine,
        client,
        FakeEmbedder(),
        ["cli-index-kept"],
        readmes({"cli-index-kept": "# A\n\na.\n"}),
    )

    code = run_index(
        engine,
        client,
        FakeEmbedder(),
        ["cli-index-kept", "cli-index-next"],
        readmes({"cli-index-next": "# B\n\nb.\n"}),
    )

    assert code == 1
    with session_scope(engine) as session:
        titles = session.execute(
            select(Document.repository, Document.section_title).where(
                Document.repository.in_(["cli-index-kept", "cli-index-next"])
            )
        ).all()
    assert sorted(titles) == [("cli-index-kept", "A"), ("cli-index-next", "B")]


def test_max_pages_rejects_values_below_one():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--all", "--max-pages", "0"])


def test_cmd_collect_all_uses_official_repositories_and_exits_on_failure(monkeypatch):
    args = build_parser().parse_args(["collect", "--all", "--max-pages", "2"])
    recorded = {}

    def fake_run_collect(engine, client, repositories, max_pages):
        recorded["repositories"] = repositories
        recorded["max_pages"] = max_pages
        return 1

    monkeypatch.setattr(cli, "make_engine", lambda database_url: object())
    monkeypatch.setattr(cli, "run_collect", fake_run_collect)

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_collect(args)

    assert exc_info.value.code == 1
    assert recorded["repositories"] == OFFICIAL_REPOSITORIES
    assert recorded["max_pages"] == 2


def test_cmd_collect_single_repository_returns_normally_on_success(monkeypatch):
    args = build_parser().parse_args(["collect", "python"])
    recorded = {}

    def fake_run_collect(engine, client, repositories, max_pages):
        recorded["repositories"] = repositories
        return 0

    monkeypatch.setattr(cli, "make_engine", lambda database_url: object())
    monkeypatch.setattr(cli, "run_collect", fake_run_collect)

    cli.cmd_collect(args)

    assert recorded["repositories"] == ("python",)


def test_cmd_index_all_uses_official_repositories_and_exits_on_failure(monkeypatch):
    args = build_parser().parse_args(["index", "--all"])
    recorded = {}

    def fake_run_index(engine, client, embedder, repositories, fetch_readme):
        recorded["repositories"] = repositories
        return 1

    monkeypatch.setattr(cli, "make_engine", lambda database_url: object())
    monkeypatch.setattr(cli, "get_embedder", lambda name: object())
    monkeypatch.setattr(cli, "run_index", fake_run_index)

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_index(args)

    assert exc_info.value.code == 1
    assert recorded["repositories"] == OFFICIAL_REPOSITORIES


def test_format_collect_summary_lists_repositories_reasons_and_errors():
    outcomes = [
        CollectOutcome("cli-ok", "end", 3, 10, 2, 1.5, None, 7),
        CollectOutcome("cli-bad", "error", 1, 5, 0, 0.2, "HTTPStatusError: boom"),
    ]

    summary = _format_collect_summary(outcomes)

    assert "cli-ok" in summary
    assert "derived" in summary.splitlines()[0]
    assert summary.splitlines()[1].split()[4] == "7"
    assert "cli-bad" in summary
    assert "end" in summary
    assert "error" in summary
    assert any("HTTPStatusError: boom" in line for line in summary.splitlines())
