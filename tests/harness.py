# 회귀 시험용 하네스 — ★네트워크·인증·실시간 대기 없음★.
#
# 여기 있는 것은 전부 "시험이 거짓으로 통과하지 않게" 하는 장치다.
# · FakeClock  — 30분·하루 같은 시간 조건을 실제로 기다리지 않고 잰다
# · StubApi    — Garmin 호출을 대신 받고 ★몇 번 불렸는지★ 를 센다
# · CallSpy    — 스텁이 실제로 불렸는지 확인한다. 안 불렸는데 통과하면 그 시험은 공허하다
# · temp_data  — data/ 를 임시 경로로 돌려 실제 개인 기록을 건드리지 않는다
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable


class FakeClock:
    """주입 가능한 시계. now() 를 콜러블로 넘겨 쓰는 코드에 그대로 꽂는다."""

    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def advance(self, **kw) -> datetime:
        self.t += timedelta(**kw)
        return self.t


class CallSpy:
    """호출 횟수와 인자를 기록한다. ★0회면 시험이 아무것도 안 잰 것이다.★"""

    def __init__(self, result: Any = None, raises: BaseException | None = None):
        self.calls: list[tuple[tuple, dict]] = []
        self._result = result
        self._raises = raises

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        if self._raises is not None:
            raise self._raises
        return self._result() if callable(self._result) else self._result

    @property
    def count(self) -> int:
        return len(self.calls)


@dataclass
class StubApi:
    """Garmin API 스텁. ★실제 호출은 절대 나가지 않는다.★

    results 에 날짜별 응답을 넣고, fail_dates 에 넣은 날짜는 예외를 던진다.
    """

    results: dict[str, Any] = field(default_factory=dict)
    fail_dates: set[str] = field(default_factory=set)
    fail_with: Callable[[], BaseException] = lambda: RuntimeError("stub 실패")
    calls: list[str] = field(default_factory=list)

    def get_stats(self, date_str: str) -> dict:
        self.calls.append(date_str)
        if date_str in self.fail_dates:
            raise self.fail_with()
        return self.results.get(date_str, {"date": date_str, "ok": True})


def temp_data(tmp_path: Path) -> Path:
    """임시 data 디렉터리. 실제 개인 운동기록 경로를 절대 쓰지 않는다."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def killswitch(data_dir: Path, on: bool = True) -> Path:
    """킬스위치 파일을 켜고 끈다. 경로만 만들고 내용은 보지 않는다."""
    p = data_dir / "garmin_killswitch"
    if on:
        p.write_text("stop")
    elif p.exists():
        p.unlink()
    return p
