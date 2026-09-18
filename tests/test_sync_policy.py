# 동기화 루프 정책 시험 — ★네트워크 없음.★ 스텁으로 실패를 만들어 루프 판단만 잰다.
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import sync_policy as sp  # noqa: E402
from harness import StubApi  # noqa: E402


# ── 연속 실패면 멈춘다 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("n,stop", [(0, False), (1, False), (2, False), (3, True), (10, True)])
def test_연속실패_한계(n, stop):
    assert sp.should_stop(n) is stop


def test_루프가_실제로_멈추는지_스텁으로_돌려본다():
    """70회를 끝까지 두드리면 차단만 깊어진다. 연속 3회에서 멈춰야 한다."""
    api = StubApi(fail_dates={f"d{i}" for i in range(70)})  # 전부 실패
    consecutive = 0
    for i in range(70):
        if sp.should_stop(consecutive):
            break
        try:
            api.get_stats(f"d{i}")
            consecutive = 0
        except RuntimeError:
            consecutive += 1
    # ★스텁이 실제로 불렸는지 센다★ — 0회면 이 시험은 아무것도 안 잰 것이다.
    assert api.calls, "스텁이 한 번도 안 불렸다"
    assert len(api.calls) == 3, f"3회에서 멈춰야 하는데 {len(api.calls)}회 돌았다"


def test_중간에_성공하면_연속카운터가_풀린다():
    # 몇 건 실패는 흔하다. 흩어진 실패로 멈추면 정상 수집이 안 된다.
    api = StubApi(fail_dates={"d0", "d1", "d3", "d4"})
    consecutive = 0
    for i in range(6):
        if sp.should_stop(consecutive):
            break
        try:
            api.get_stats(f"d{i}")
            consecutive = 0
        except RuntimeError:
            consecutive += 1
    assert len(api.calls) == 6, "흩어진 실패로 멈췄다"


# ── 빈 결과로 덮어쓰지 않는다 ────────────────────────────────────────────────

def test_빈_결과는_기존_기록을_안_덮는다():
    # ★이게 기록이 통째로 사라지는 경로다.★
    assert sp.should_write([], [{"date": "2026-09-06"}] * 40) is False


def test_이전에도_없었으면_빈_결과를_쓴다():
    # 빈 파일을 만드는 것은 손실이 아니다.
    assert sp.should_write([], []) is True
    assert sp.should_write([], None) is True


def test_줄어든_결과는_쓴다():
    # 기간이 줄었을 수 있다. 여기서 막으면 정상 축소가 영영 반영되지 않는다.
    assert sp.should_write([1, 2], [1, 2, 3, 4, 5]) is True


def test_늘어난_결과도_쓴다():
    assert sp.should_write([1, 2, 3], [1]) is True


def test_안_쓸_때는_이유가_남는다():
    # ★이유를 안 남기면 '조용히 안 씀' 이 되어 원래 문제와 같아진다.★
    reason = sp.write_skip_reason([], [1, 2, 3])
    assert reason and "3건" in reason


def test_쓸_때는_이유가_없다():
    assert sp.write_skip_reason([1], []) is None


# ── 공허한 통과 방지 ─────────────────────────────────────────────────────────

def test_정책이_파일을_건드리지_않는다():
    src = (ROOT / "sync_policy.py").read_text(encoding="utf-8")
    for banned in ("open(", "Path(", "json.", "read_text", "write_text"):
        assert banned not in src, f"sync_policy 가 {banned} 를 쓴다 — 순수해야 한다"


def test_should_write_가_양쪽_답을_모두_낸다():
    # 항상 True 를 돌려줘도 위 단정 대부분이 통과한다. 두 답이 다 나오는지 본다.
    answers = {sp.should_write([], [1]), sp.should_write([1], [])}
    assert answers == {True, False}
