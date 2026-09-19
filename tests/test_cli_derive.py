# tests/test_cli_derive.py
"""derive 명령의 대상 선택, 실패 격리, 출력."""

import pytest

from whatfrom import cli
from whatfrom.cli import build_parser, run_derive
from whatfrom.collect.derive import DeriveOutcome
from whatfrom.collect.sync import OFFICIAL_REPOSITORIES


def test_derive_requires_exactly_one_target():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["derive", "python", "--all"])
    with pytest.raises(SystemExit):
        parser.parse_args(["derive"])


def test_cmd_derive_all_uses_official_repositories_and_exits_on_failure(monkeypatch):
    args = build_parser().parse_args(["derive", "--all"])
    recorded = {}

    def fake_run_derive(engine, repositories):
        recorded["repositories"] = repositories
        return 1

    monkeypatch.setattr(cli, "make_engine", lambda database_url: object())
    monkeypatch.setattr(cli, "run_derive", fake_run_derive)

    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_derive(args)

    assert exc_info.value.code == 1
    assert recorded["repositories"] == OFFICIAL_REPOSITORIES


def test_run_derive_keeps_going_and_reports_failure(engine, monkeypatch, capsys):
    def fake_derive(session, repository):
        if repository == "broken":
            raise RuntimeError("boom")
        return DeriveOutcome(repository, 200, 150, 3, 2, 190)

    monkeypatch.setattr(cli, "derive_repository", fake_derive)

    code = run_derive(engine, ["broken", "fine"])

    out = capsys.readouterr().out
    assert code == 1
    assert "broken" in out and "RuntimeError: boom" in out
    assert "fine" in out
    assert "tags   200" in out
    assert "changed   150" in out
    assert "conflicts 3 fields / 2 tags" in out
    assert "distribution 95.0%" in out


def test_run_derive_returns_zero_when_every_repository_succeeds(engine, monkeypatch):
    monkeypatch.setattr(
        cli,
        "derive_repository",
        lambda session, repository: DeriveOutcome(repository, 0, 0, 0, 0, 0),
    )

    assert run_derive(engine, ["empty"]) == 0
