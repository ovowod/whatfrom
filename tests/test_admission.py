"""추천 작업 수 상한. 자리는 정확히 한 번 반환되고, 자리 밖에서 추천 작업이 실행되지 않는다.

asyncio의 Task.cancel()은 anyio의 스레드 보호를 뚫고 기다리는 쪽을 먼저 끝낸다(스펙 §2).
그래서 기다리는 쪽이 끝났다고 스레드 작업이 끝났다고 볼 수 없다.
"""

import asyncio
import threading
from collections.abc import Callable

import anyio
import anyio.to_thread
import pytest

from whatfrom.admission import RecommendationLimiter, run_admitted


class Blocker:
    """풀려날 때까지 스레드를 붙잡는다. 불린 횟수와 끝난 여부를 남긴다."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def __call__(self) -> str:
        self.calls += 1
        self.entered.set()
        self.release.wait(10)
        self.finished.set()
        return "done"


async def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    with anyio.fail_after(timeout):
        while not condition():
            await anyio.sleep(0.01)


def test_try_acquire_does_not_wait_when_every_seat_is_taken():
    limiter = RecommendationLimiter(2)

    assert limiter.try_acquire() is not None
    assert limiter.try_acquire() is not None
    assert limiter.try_acquire() is None
    assert limiter.active == 2


def test_a_limit_below_one_is_rejected():
    with pytest.raises(ValueError):
        RecommendationLimiter(0)


def test_a_started_ticket_returns_its_seat_once_when_it_finishes():
    limiter = RecommendationLimiter(1)
    ticket = limiter.try_acquire()

    assert ticket.start() is True
    ticket.abandon()  # 이미 실행 중이면 핸들러는 아무것도 하지 않는다
    assert limiter.active == 1
    ticket.finish()
    assert limiter.active == 0
    with pytest.raises(AssertionError):
        ticket.finish()


def test_a_ticket_abandoned_before_it_starts_never_runs():
    """스레드가 래퍼를 부르기 직전에 취소가 오는 경합. 뒤늦게 시작한 래퍼는 실행하지 않는다."""
    limiter = RecommendationLimiter(1)
    ticket = limiter.try_acquire()

    ticket.abandon()
    assert limiter.active == 0
    assert ticket.start() is False
    assert limiter.active == 0
    with pytest.raises(AssertionError):
        ticket.abandon()


def test_run_admitted_returns_the_result_and_reports_the_start_once():
    limiter = RecommendationLimiter(1)
    starts: list[int] = []

    async def main() -> str:
        return await run_admitted(limiter.try_acquire(), lambda: "ok", lambda: starts.append(1))

    assert anyio.run(main) == "ok"
    assert starts == [1]
    assert limiter.active == 0


def test_run_admitted_returns_the_seat_when_the_work_raises():
    limiter = RecommendationLimiter(1)

    def boom() -> str:
        raise ValueError("bug")

    async def main() -> None:
        await run_admitted(limiter.try_acquire(), boom)

    with pytest.raises(ValueError):
        anyio.run(main)
    assert limiter.active == 0


def test_task_cancel_keeps_the_seat_until_the_thread_work_finishes():
    limiter = RecommendationLimiter(1)
    work = Blocker()

    async def main() -> None:
        task = asyncio.create_task(run_admitted(limiter.try_acquire(), work))
        await wait_until(work.entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 기다리던 쪽은 끝났지만 스레드 작업은 아직 돈다. 자리는 그대로여야 한다.
        assert not work.finished.is_set()
        assert limiter.active == 1
        assert limiter.try_acquire() is None

        work.release.set()
        await wait_until(lambda: limiter.active == 0)

    anyio.run(main)
    assert work.calls == 1


def test_anyio_cancellation_waits_for_the_thread_work():
    """anyio 취소도 Task.cancel과 같은 결과여야 한다(스펙 §10): 스레드가 시작된 뒤라면
    작업이 끝날 때까지 자리를 돌려주지 않는다."""
    limiter = RecommendationLimiter(1)
    work = Blocker()
    scopes: list[anyio.CancelScope] = []

    async def runner() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await run_admitted(limiter.try_acquire(), work)

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(runner)
            await wait_until(work.entered.is_set)
            scopes[0].cancel()
            await anyio.sleep(0.01)  # 루프에게 취소를 처리할 틈을 준다

            # 스레드가 시작된 뒤의 취소는 작업이 끝날 때까지 미뤄야 한다.
            assert not work.finished.is_set()
            assert limiter.active == 1
            assert limiter.try_acquire() is None

            work.release.set()
            await wait_until(lambda: limiter.active == 0)

        assert work.finished.is_set()

    anyio.run(main)
    assert work.calls == 1


def test_cancel_before_the_thread_starts_returns_the_seat_and_skips_the_work():
    limiter = RecommendationLimiter(1)
    occupier = Blocker()
    work = Blocker()

    async def main() -> None:
        # 스레드 토큰을 하나로 줄이고 다른 작업이 쥐게 해, 추천 작업이 토큰을 기다리게 한다.
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1
        async with anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, occupier)
            await wait_until(occupier.entered.is_set)

            task = asyncio.create_task(run_admitted(limiter.try_acquire(), work))
            await anyio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert limiter.active == 0

            occupier.release.set()

    anyio.run(main)
    assert work.calls == 0
