# tests/collect/test_sync.py
"""리포지토리 수집 흐름과 collection_runs 기록.

실제 DB에 커밋한다. 페이지마다 트랜잭션을 여는 동작 자체가 검증 대상이라
롤백 세션으로는 확인할 수 없다. 테스트가 쓴 행은 cleanup 픽스처가 지운다.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx2
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from whatfrom.collect import sync
from whatfrom.collect.hub import HubClient
from whatfrom.collect.sync import (
    STOP_END,
    STOP_ERROR,
    STOP_INTERRUPTED,
    STOP_MAX_PAGES,
    STOP_OFFSET_LIMIT,
    collect_all,
    collect_repository,
)
from whatfrom.core.db import session_scope
from whatfrom.core.models import CollectionRun, ImageTag, ImageVariant, Repository

NOW = datetime(2026, 9, 14, tzinfo=UTC)
OFFSET_LIMIT_BODY = {"message": "pagination offset too large for anonymous requests"}


def tag(name: str, image_os: str = "linux", duplicate_variant: bool = False) -> dict:
    image = {"os": image_os, "architecture": "amd64", "digest": f"sha256:{name}", "size": 1}
    return {
        "name": name,
        "digest": f"sha256:index-{name}",
        "tag_last_pushed": "2026-09-01T00:00:00Z",
        "images": [image, image] if duplicate_variant else [image],
    }


class FakeHub:
    """URL 경로로 응답을 정한다.

    부분문자열로 매칭하면 리포지토리 경로가 태그 목록 경로에도 걸린다.
    """

    def __init__(self) -> None:
        self.routes: dict[str, httpx2.Response | BaseException] = {}
        self.calls: list[str] = []

    def repository(self, name: str) -> None:
        self.routes[f"/v2/repositories/library/{name}/"] = httpx2.Response(
            200, json={"name": name, "namespace": "library", "full_description": "# x"}
        )

    def pages(self, name: str, pages: list[httpx2.Response | BaseException]) -> None:
        for number, response in enumerate(pages, start=1):
            path = (
                f"/v2/repositories/library/{name}/tags" if number == 1 else f"/next/{name}/{number}"
            )
            self.routes[path] = response

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(str(request.url))
        response = self.routes.get(request.url.path)
        if response is None:
            return httpx2.Response(404)
        if isinstance(response, BaseException):
            raise response
        return response

    def client(self) -> HubClient:
        http = httpx2.Client(transport=httpx2.MockTransport(self.handler))
        return HubClient(http, sleep=lambda _seconds: None)


def page(name: str, tags: list[dict], number: int, last: bool) -> httpx2.Response:
    following = None if last else f"https://hub.docker.com/next/{name}/{number + 1}"
    return httpx2.Response(200, json={"next": following, "results": tags})


@pytest.fixture
def cleanup(engine):
    names: list[str] = []
    yield names
    with session_scope(engine) as session:
        for name in names:
            tag_ids = select(ImageTag.id).where(ImageTag.repository == name)
            session.execute(delete(ImageVariant).where(ImageVariant.tag_id.in_(tag_ids)))
            session.execute(delete(ImageTag).where(ImageTag.repository == name))
            session.execute(delete(Repository).where(Repository.name == name))
            session.execute(delete(CollectionRun).where(CollectionRun.repository == name))


def runs(engine, name: str) -> list[CollectionRun]:
    with session_scope(engine) as session:
        return list(
            session.execute(
                select(CollectionRun)
                .where(CollectionRun.repository == name)
                .order_by(CollectionRun.id)
            ).scalars()
        )


def stored_tags(engine, name: str) -> list[str]:
    with session_scope(engine) as session:
        return sorted(
            session.execute(select(ImageTag.tag).where(ImageTag.repository == name)).scalars()
        )


def test_reaching_the_last_page_completes_with_end(engine, cleanup):
    cleanup.append("sync-end")
    hub = FakeHub()
    hub.repository("sync-end")
    hub.pages("sync-end", [page("sync-end", [tag("a"), tag("b")], 1, last=True)])

    outcome = collect_repository(engine, hub.client(), "sync-end", None, NOW)

    assert outcome.stop_reason == STOP_END
    assert (outcome.pages, outcome.tags_seen, outcome.tags_written) == (1, 2, 2)
    [run] = runs(engine, "sync-end")
    assert run.stop_reason == STOP_END
    assert run.finished_at is not None
    assert (run.pages, run.tags_seen, run.tags_written) == (1, 2, 2)


def test_collecting_the_same_page_again_writes_nothing(engine, cleanup):
    """같은 페이로드를 다시 받으면 두 번째 실행은 아무것도 새로 쓰지 않는다."""
    cleanup.append("sync-repeat")
    hub = FakeHub()
    hub.repository("sync-repeat")
    hub.pages("sync-repeat", [page("sync-repeat", [tag("a"), tag("b")], 1, last=True)])

    collect_repository(engine, hub.client(), "sync-repeat", None, NOW)
    outcome = collect_repository(engine, hub.client(), "sync-repeat", None, NOW)

    assert (outcome.tags_seen, outcome.tags_written) == (2, 0)
    [_, second_run] = runs(engine, "sync-repeat")
    assert (second_run.tags_seen, second_run.tags_written) == (2, 0)


def test_collecting_again_still_writes_a_tag_missing_its_digest(engine, cleanup):
    """digest 없는 태그는 이미지가 같은지 알 수 없으니 다시 받아도 변경으로 센다."""
    cleanup.append("sync-repeat-nodigest")
    hub = FakeHub()
    hub.repository("sync-repeat-nodigest")
    tags = [tag("a"), tag("b")]
    del tags[0]["digest"]
    hub.pages("sync-repeat-nodigest", [page("sync-repeat-nodigest", tags, 1, last=True)])

    collect_repository(engine, hub.client(), "sync-repeat-nodigest", None, NOW)
    outcome = collect_repository(engine, hub.client(), "sync-repeat-nodigest", None, NOW)

    assert outcome.tags_written == 1
    [_, second_run] = runs(engine, "sync-repeat-nodigest")
    assert second_run.tags_written == 1


def test_the_offset_limit_completes_without_retrying(engine, cleanup):
    cleanup.append("sync-offset")
    hub = FakeHub()
    hub.repository("sync-offset")
    hub.pages(
        "sync-offset",
        [
            page("sync-offset", [tag("a")], 1, last=False),
            httpx2.Response(403, json=OFFSET_LIMIT_BODY),
        ],
    )

    outcome = collect_repository(engine, hub.client(), "sync-offset", None, NOW)

    assert outcome.stop_reason == STOP_OFFSET_LIMIT
    assert sum("/next/sync-offset/2" in call for call in hub.calls) == 1
    [run] = runs(engine, "sync-offset")
    assert run.finished_at is not None


def test_other_forbidden_responses_are_recorded_as_errors(engine, cleanup):
    cleanup.append("sync-forbidden")
    hub = FakeHub()
    hub.repository("sync-forbidden")
    hub.pages(
        "sync-forbidden",
        [
            page("sync-forbidden", [tag("a")], 1, last=False),
            httpx2.Response(403, json={"message": "forbidden"}),
        ],
    )

    with pytest.raises(httpx2.HTTPStatusError):
        collect_repository(engine, hub.client(), "sync-forbidden", None, NOW)

    [run] = runs(engine, "sync-forbidden")
    assert run.stop_reason == STOP_ERROR
    assert run.finished_at is None


def test_max_pages_with_pages_left_stops_with_max_pages(engine, cleanup):
    cleanup.append("sync-max")
    hub = FakeHub()
    hub.repository("sync-max")
    hub.pages(
        "sync-max",
        [
            page("sync-max", [tag("a")], 1, last=False),
            page("sync-max", [tag("b")], 2, last=True),
        ],
    )

    outcome = collect_repository(engine, hub.client(), "sync-max", 1, NOW)

    assert outcome.stop_reason == STOP_MAX_PAGES
    assert stored_tags(engine, "sync-max") == ["a"]


def test_max_pages_on_the_last_page_is_end(engine, cleanup):
    """더 받을 페이지가 없으면 제한에 닿았어도 범위를 다 받은 것이다."""
    cleanup.append("sync-max-end")
    hub = FakeHub()
    hub.repository("sync-max-end")
    hub.pages("sync-max-end", [page("sync-max-end", [tag("a")], 1, last=True)])

    outcome = collect_repository(engine, hub.client(), "sync-max-end", 1, NOW)

    assert outcome.stop_reason == STOP_END


def test_a_run_is_recorded_even_when_the_first_request_fails(engine, cleanup):
    cleanup.append("sync-missing")
    hub = FakeHub()

    with pytest.raises(httpx2.HTTPStatusError):
        collect_repository(engine, hub.client(), "sync-missing", None, NOW)

    [run] = runs(engine, "sync-missing")
    assert run.stop_reason == STOP_ERROR
    assert run.finished_at is None


def test_a_failed_variant_insert_rolls_back_only_that_page(engine, cleanup):
    """태그와 변종이 같은 트랜잭션이어야 한다. 변종만 실패하고 태그가 남으면 안 된다."""
    cleanup.append("sync-atomic")
    hub = FakeHub()
    hub.repository("sync-atomic")
    hub.pages(
        "sync-atomic",
        [
            page("sync-atomic", [tag("a")], 1, last=False),
            page("sync-atomic", [tag("b", duplicate_variant=True)], 2, last=True),
        ],
    )

    with pytest.raises(IntegrityError):
        collect_repository(engine, hub.client(), "sync-atomic", None, NOW)

    assert stored_tags(engine, "sync-atomic") == ["a"]
    [run] = runs(engine, "sync-atomic")
    assert (run.pages, run.tags_seen, run.stop_reason) == (1, 1, STOP_ERROR)


def test_a_failed_counter_update_rolls_back_that_page(engine, cleanup, monkeypatch):
    """카운터와 데이터가 같은 트랜잭션이어야 한다. 기록과 실제 저장이 어긋나면 안 된다."""
    cleanup.append("sync-counter")
    hub = FakeHub()
    hub.repository("sync-counter")
    hub.pages(
        "sync-counter",
        [
            page("sync-counter", [tag("a")], 1, last=False),
            page("sync-counter", [tag("b")], 2, last=True),
        ],
    )
    original = sync._add_page_counts
    calls = {"n": 0}

    def fail_on_second_page(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("counter update failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(sync, "_add_page_counts", fail_on_second_page)

    with pytest.raises(RuntimeError):
        collect_repository(engine, hub.client(), "sync-counter", None, NOW)

    assert stored_tags(engine, "sync-counter") == ["a"]
    [run] = runs(engine, "sync-counter")
    assert run.pages == 1


def test_an_interrupt_is_recorded_and_raised_again(engine, cleanup):
    cleanup.append("sync-interrupt")
    hub = FakeHub()
    hub.repository("sync-interrupt")
    hub.pages(
        "sync-interrupt",
        [page("sync-interrupt", [tag("a")], 1, last=False), KeyboardInterrupt()],
    )

    with pytest.raises(KeyboardInterrupt):
        collect_repository(engine, hub.client(), "sync-interrupt", None, NOW)

    [run] = runs(engine, "sync-interrupt")
    assert run.stop_reason == STOP_INTERRUPTED
    assert run.finished_at is None
    assert stored_tags(engine, "sync-interrupt") == ["a"]


def test_collect_all_isolates_a_failing_repository(engine, cleanup):
    cleanup.extend(["sync-all-bad", "sync-all-ok"])
    hub = FakeHub()
    hub.repository("sync-all-ok")
    hub.pages("sync-all-ok", [page("sync-all-ok", [tag("a")], 1, last=True)])

    outcomes = collect_all(engine, hub.client(), ["sync-all-bad", "sync-all-ok"], None, NOW)

    assert [(o.repository, o.stop_reason) for o in outcomes] == [
        ("sync-all-bad", STOP_ERROR),
        ("sync-all-ok", STOP_END),
    ]
    assert outcomes[0].error is not None
    assert stored_tags(engine, "sync-all-ok") == ["a"]


def test_collect_all_reports_partial_counts_for_a_failed_repository(engine, cleanup):
    """실패해도 성공한 페이지의 카운트는 실행 행에서 읽어 보고한다."""
    cleanup.append("sync-all-partial")
    hub = FakeHub()
    hub.repository("sync-all-partial")
    hub.pages(
        "sync-all-partial",
        [
            page("sync-all-partial", [tag("a"), tag("b")], 1, last=False),
            httpx2.Response(403, json={"message": "forbidden"}),
        ],
    )

    [outcome] = collect_all(engine, hub.client(), ["sync-all-partial"], None, NOW)

    assert outcome.stop_reason == STOP_ERROR
    assert outcome.pages == 1
    assert outcome.tags_seen == 2


def fail_start_for(monkeypatch, name: str) -> None:
    original = sync._start_run

    def start(engine, repository, *args, **kwargs):
        if repository == name:
            raise RuntimeError("db down")
        return original(engine, repository, *args, **kwargs)

    monkeypatch.setattr(sync, "_start_run", start)


def test_collect_all_does_not_report_an_earlier_run_when_starting_fails(
    engine, cleanup, monkeypatch
):
    """실행 행을 만들지 못하면 이전 실행의 결과가 아니라 이번 실패를 보고한다."""
    cleanup.extend(["sync-start-bad", "sync-start-ok"])
    hub = FakeHub()
    for name in ("sync-start-bad", "sync-start-ok"):
        hub.repository(name)
        hub.pages(name, [page(name, [tag("a")], 1, last=True)])
    collect_repository(engine, hub.client(), "sync-start-bad", None, NOW)
    fail_start_for(monkeypatch, "sync-start-bad")

    outcomes = collect_all(engine, hub.client(), ["sync-start-bad", "sync-start-ok"], None, NOW)

    bad, ok = outcomes
    assert (bad.repository, bad.stop_reason, bad.error) == (
        "sync-start-bad",
        STOP_ERROR,
        "RuntimeError: db down",
    )
    assert (bad.pages, bad.tags_seen, bad.tags_written) == (0, 0, 0)
    assert (ok.repository, ok.stop_reason) == ("sync-start-ok", STOP_END)
    assert stored_tags(engine, "sync-start-ok") == ["a"]


def test_collect_all_survives_a_start_failure_without_earlier_runs(engine, cleanup, monkeypatch):
    cleanup.append("sync-start-first")
    fail_start_for(monkeypatch, "sync-start-first")

    [outcome] = collect_all(engine, FakeHub().client(), ["sync-start-first"], None, NOW)

    assert (outcome.stop_reason, outcome.error) == (STOP_ERROR, "RuntimeError: db down")
    assert runs(engine, "sync-start-first") == []


def test_collect_all_lets_an_interrupt_stop_the_batch(engine, cleanup):
    cleanup.extend(["sync-stop-first", "sync-stop-later"])
    hub = FakeHub()
    hub.repository("sync-stop-first")
    hub.pages("sync-stop-first", [KeyboardInterrupt()])
    hub.repository("sync-stop-later")
    hub.pages("sync-stop-later", [page("sync-stop-later", [tag("a")], 1, last=True)])

    with pytest.raises(KeyboardInterrupt):
        collect_all(engine, hub.client(), ["sync-stop-first", "sync-stop-later"], None, NOW)

    assert not any("sync-stop-later" in call for call in hub.calls)
    assert runs(engine, "sync-stop-later") == []


def test_a_failure_to_record_the_error_keeps_the_original_exception(engine, cleanup, monkeypatch):
    cleanup.append("sync-record-fail")

    def fail(*args, **kwargs):
        raise RuntimeError("recording failed")

    monkeypatch.setattr(sync, "_finish_run", fail)

    with pytest.raises(httpx2.HTTPStatusError):
        collect_repository(engine, FakeHub().client(), "sync-record-fail", None, NOW)


def test_collect_all_measures_elapsed_time_per_repository(engine, cleanup, monkeypatch):
    cleanup.extend(["sync-time-a", "sync-time-b"])
    hub = FakeHub()
    for name in ("sync-time-a", "sync-time-b"):
        hub.repository(name)
        hub.pages(name, [page(name, [tag("a")], 1, last=True)])
    # sync 모듈의 time만 바꾼다. 전역 time.monotonic을 바꾸면 다른 호출이 값을 소모한다.
    ticks = iter([10.0, 12.5, 20.0, 20.25])
    monkeypatch.setattr(sync, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    outcomes = collect_all(engine, hub.client(), ["sync-time-a", "sync-time-b"], None, NOW)

    assert [o.elapsed_seconds for o in outcomes] == [2.5, 0.25]


def test_started_at_is_the_wall_clock_not_the_batch_time(engine, cleanup):
    cleanup.append("sync-clock")
    hub = FakeHub()
    hub.repository("sync-clock")
    hub.pages("sync-clock", [page("sync-clock", [tag("a")], 1, last=True)])
    long_ago = datetime(2000, 1, 1, tzinfo=UTC)

    collect_repository(engine, hub.client(), "sync-clock", None, long_ago)

    [run] = runs(engine, "sync-clock")
    assert datetime.now(UTC) - run.started_at < timedelta(minutes=1)
    assert run.finished_at >= run.started_at
