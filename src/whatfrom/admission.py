# src/whatfrom/admission.py
"""추천 작업 수 상한(스펙 F11-a).

상한을 넘는 요청을 받아 두면 스레드를 기다리는 동안 클라이언트가 포기하고, 서버는 그 뒤에
헛일을 한다(F10 기준선). 그래서 자리가 없으면 기다리지 않고 거절한다.

자리는 요청마다 표(Ticket)로 관리한다. asyncio의 Task.cancel()은 anyio의 스레드 보호를
뚫고 기다리는 쪽을 먼저 끝내므로, 기다리는 쪽이 끝났다고 스레드 작업이 끝났다고 볼 수 없다.
표의 상태를 대기 → 실행 → 완료, 대기 → 취소로만 바꾸고 모든 전이를 락 하나로 보호해,
자리는 정확히 한 번 반환되고 자리 밖에서 추천 작업이 실행되지 않는다.
"""

import threading
from collections.abc import Callable
from typing import cast

import anyio.to_thread

REJECTED_DETAIL = "추천 요청이 많아 지금은 처리할 수 없습니다. 잠시 후 다시 시도하세요."

_WAITING = "waiting"
_RUNNING = "running"
_DONE = "done"
_CANCELLED = "cancelled"


class RecommendationLimiter:
    """수락해 자리를 점유한 추천 작업 수의 상한. 앱 인스턴스(워커)마다 하나다."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"추천 작업 수 상한은 1 이상이어야 한다: {limit}")
        self.limit = limit
        # 이벤트 루프(try_acquire, abandon)와 작업 스레드(start, finish)가 함께 쓴다.
        # 짧은 확인과 산술만 감싸고 기다리는 동작이 없다.
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> "Ticket | None":
        """자리가 있으면 차감하고 표를 준다. 없으면 기다리지 않고 None이다."""
        with self._lock:
            if self._active >= self.limit:
                return None
            self._active += 1
            return Ticket(self)

    def _release(self) -> None:
        # 락을 쥔 채로만 부른다.
        assert self._active > 0, "자리를 두 번 반환했다"
        self._active -= 1


class Ticket:
    """수락된 요청 하나의 자리."""

    def __init__(self, limiter: RecommendationLimiter) -> None:
        self._limiter = limiter
        self._state = _WAITING

    def start(self) -> bool:
        """작업 스레드 쪽. 대기 → 실행이면 True. 이미 취소됐으면 False이고 실행하지 않는다."""
        with self._limiter._lock:
            if self._state == _CANCELLED:
                return False
            assert self._state == _WAITING, f"시작할 수 없는 상태: {self._state}"
            self._state = _RUNNING
            return True

    def finish(self) -> None:
        """작업 스레드 쪽. 실행 → 완료, 자리를 반환한다."""
        with self._limiter._lock:
            assert self._state == _RUNNING, f"끝낼 수 없는 상태: {self._state}"
            self._state = _DONE
            self._limiter._release()

    def abandon(self) -> None:
        """핸들러 쪽. 대기 중이면 취소하고 자리를 반환한다. 실행·완료 상태면 아무 일도 안 한다."""
        with self._limiter._lock:
            assert self._state != _CANCELLED, "표를 두 번 취소했다"
            if self._state == _WAITING:
                self._state = _CANCELLED
                self._limiter._release()


async def run_admitted[T](
    ticket: Ticket, func: Callable[[], T], on_start: Callable[[], None] | None = None
) -> T:
    """스레드에서 func를 실행한다. 자리는 작업이 끝나거나, 시작 전에 취소되면 돌려준다."""

    def work() -> T | None:
        if not ticket.start():
            # 기다리던 쪽이 먼저 취소하고 자리를 돌려줬다. 자리 밖에서 실행하지 않는다.
            return None
        try:
            if on_start is not None:
                on_start()
            return func()
        finally:
            ticket.finish()

    try:
        result = await anyio.to_thread.run_sync(work)
    except BaseException:
        ticket.abandon()
        raise
    # 정상으로 돌아왔다면 작업이 시작해 끝난 것이다(None은 취소된 뒤에만 나온다).
    return cast(T, result)
