# src/whatfrom/eval/timing.py
"""이름으로 지정한 메서드의 호출 시간과 인자를 기록하는 프록시.

평가 러너가 추천 경로의 단계별 시간과 LLM #2에 보낸 프롬프트를 남기는 데 쓴다.
eval은 recommend를 import하지 않는다(계층 규칙). 그래서 임베더·LLM 공급자의 프로토콜을
가져오지 않고, 이름으로 지정한 메서드만 감싸는 범용 프록시로 만든다.
"""

import time
from collections.abc import Callable, Iterable
from typing import Any


class Timed:
    """target을 그대로 흉내 내면서 methods에 있는 메서드를 감싼다.

    호출 시간은 seconds에 더하고, 마지막 호출의 인자는 calls에 남긴다. 문항마다 새로
    만든다. 예외가 난 호출(시간 초과 포함)도 걸린 시간을 남긴다. clock은 테스트가
    가짜 시계를 넣을 수 있게 둔다.
    """

    def __init__(
        self,
        target: Any,
        methods: Iterable[str],
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._target = target
        self._methods = frozenset(methods)
        self._clock = clock
        self.seconds: dict[str, float] = {}
        self.calls: dict[str, tuple[tuple, dict]] = {}

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if name not in self._methods:
            return value

        def timed(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] = (args, kwargs)
            start = self._clock()
            try:
                return value(*args, **kwargs)
            finally:
                self.seconds[name] = self.seconds.get(name, 0.0) + self._clock() - start

        return timed
