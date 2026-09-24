"""이름으로 지정한 메서드의 호출 시간과 인자를 기록하는 프록시."""

import pytest

from whatfrom.eval.timing import Timed


class Target:
    dimension = 1024

    def embed(self, texts: list[str]) -> list[str]:
        return texts

    def fail(self) -> None:
        raise RuntimeError("down")

    def untimed(self) -> str:
        return "ok"


def clock(*ticks: float):
    """perf_counter 대신 쓰는 가짜 시계. 부를 때마다 다음 값을 돌려준다."""
    values = iter(ticks)
    return lambda: next(values)


def test_a_named_method_is_timed_exactly_and_still_returns_its_result():
    timed = Timed(Target(), ["embed"], clock=clock(10.0, 12.5))

    assert timed.embed(["q"]) == ["q"]
    assert timed.seconds == {"embed": 2.5}


def test_calls_to_the_same_method_add_up():
    timed = Timed(Target(), ["embed"], clock=clock(0.0, 1.0, 5.0, 8.0))

    timed.embed(["a"])
    timed.embed(["b"])

    assert timed.seconds == {"embed": 4.0}


def test_a_failed_call_is_still_timed():
    """시간이 초과된 호출도 걸린 시간이 남아야 어느 단계가 느렸는지 안다."""
    timed = Timed(Target(), ["fail"], clock=clock(1.0, 4.0))

    with pytest.raises(RuntimeError):
        timed.fail()

    assert timed.seconds == {"fail": 3.0}


def test_the_last_call_arguments_are_kept():
    """추천 호출의 프롬프트를 남겨 두 실행의 실제 입력을 비교한다."""
    timed = Timed(Target(), ["embed"])

    timed.embed(["first"])
    timed.embed(texts=["second"])

    assert timed.calls == {"embed": ((), {"texts": ["second"]})}


def test_other_attributes_pass_through_untimed():
    timed = Timed(Target(), ["embed"])

    assert timed.dimension == 1024
    assert timed.untimed() == "ok"
    assert (timed.seconds, timed.calls) == ({}, {})


def test_each_proxy_keeps_its_own_record():
    """문항마다 새 프록시를 만든다. 앞 문항의 기록이 섞이면 안 된다."""
    target = Target()
    first, second = Timed(target, ["embed"]), Timed(target, ["embed"])

    first.embed(["a"])

    assert "embed" in first.seconds
    assert (second.seconds, second.calls) == ({}, {})
