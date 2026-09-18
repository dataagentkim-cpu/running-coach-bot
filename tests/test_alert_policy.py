# 알림 정책 시험 — ★시각을 주입한다. 실제로 30분·하루를 기다리지 않는다.★
# 정책은 상태 파일을 모른다. 값을 넣고 판정을 받는다.
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import alert_policy as ap  # noqa: E402
from harness import FakeClock  # noqa: E402

T0 = datetime(2026, 9, 18, 9, 0, 0)


def fail(now, fail_since=None, notified=None, kind=ap.OTHER, success=None, step=0):
    return ap.decide(now, success, fail_since, notified, kind, step)


def ok(now, fail_since=None, notified=None, success=None):
    return ap.decide(now, success, fail_since, notified, ap.OK)


# ── 첫 실패는 30분 지속돼야 알린다 ───────────────────────────────────────────

def test_실패_직후에는_안_알린다():
    # 잠깐 끊긴 것으로 알리면 알림이 소음이 된다.
    d = fail(T0)
    assert d.alert is None
    assert d.fail_since == T0  # 실패 시작 시각은 이때부터 센다


@pytest.mark.parametrize("mins,expected_alert", [(1, False), (29, False), (30, True), (31, True)])
def test_30분_경계(mins, expected_alert):
    c = FakeClock(T0)
    c.advance(minutes=mins)
    d = fail(c.now(), fail_since=T0)
    assert (d.alert is not None) is expected_alert


def test_첫_알림에_지속시간이_들어간다():
    d = fail(T0 + timedelta(minutes=45), fail_since=T0)
    assert "45분" in d.alert
    assert d.last_notified_at == T0 + timedelta(minutes=45)


# ── 억제와 재알림 ────────────────────────────────────────────────────────────

def test_하루_안에는_두번째를_막는다():
    notified = T0
    d = fail(T0 + timedelta(hours=5), fail_since=T0 - timedelta(hours=1), notified=notified)
    assert d.alert is None
    assert d.last_notified_at == notified  # 억제해도 기록은 유지된다


def test_하루가_지나면_다시_알린다():
    # ★억제가 영구가 되면 그게 다시 조용한 실패다.★
    d = fail(T0 + timedelta(days=1, minutes=1), fail_since=T0 - timedelta(hours=1), notified=T0)
    assert d.alert is not None
    assert d.last_notified_at == T0 + timedelta(days=1, minutes=1)


@pytest.mark.parametrize("hours,alerts", [(23, False), (24, True), (25, True)])
def test_재알림_경계(hours, alerts):
    d = fail(T0 + timedelta(hours=hours), fail_since=T0, notified=T0)
    assert (d.alert is not None) is alerts


# ── 인증 실패는 즉시 ─────────────────────────────────────────────────────────

def test_auth_는_30분을_안_기다린다():
    # 사람이 손대야 풀리고 그동안 모든 호출이 막힌다.
    d = fail(T0, kind=ap.AUTH)
    assert d.alert is not None
    assert "인증" in d.alert


def test_auth_도_하루_억제는_받는다():
    # 즉시 알린다고 매 폴링마다 보내면 5분마다 알림이 온다.
    d = fail(T0 + timedelta(hours=1), fail_since=T0, notified=T0, kind=ap.AUTH)
    assert d.alert is None


def test_auth_는_하루_뒤_다시_알린다():
    d = fail(T0 + timedelta(days=1, minutes=1), fail_since=T0, notified=T0, kind=ap.AUTH)
    assert d.alert is not None


# ── 복구 ─────────────────────────────────────────────────────────────────────

def test_알린_뒤_복구되면_복구를_알린다():
    d = ok(T0 + timedelta(hours=2), fail_since=T0, notified=T0 + timedelta(minutes=30))
    assert d.alert is not None and "복구" in d.alert
    assert "2시간" in d.alert


def test_안_알린_실패의_복구는_안_알린다():
    # 사용자가 모르는 실패가 끝난 것을 알릴 이유가 없다.
    d = ok(T0 + timedelta(minutes=5), fail_since=T0, notified=None)
    assert d.alert is None


def test_복구는_실패흔적을_지운다():
    d = ok(T0 + timedelta(hours=2), fail_since=T0, notified=T0 + timedelta(minutes=30))
    assert d.fail_since is None
    assert d.last_notified_at is None
    assert d.last_success_at == T0 + timedelta(hours=2)


def test_계속_성공중이면_아무것도_안_한다():
    d = ok(T0, fail_since=None, notified=None)
    assert d.alert is None
    assert d.last_success_at == T0


# ── 전이 시나리오 (시계를 굴려 한 번에 본다) ─────────────────────────────────

def test_정상_실패_억제_재알림_복구_전이():
    c = FakeClock(T0)
    fs = ln = ls = None
    sent = []

    def step(kind):
        nonlocal fs, ln, ls
        d = ap.decide(c.now(), ls, fs, ln, kind)
        fs, ln, ls = d.fail_since, d.last_notified_at, d.last_success_at
        if d.alert:
            sent.append((c.now(), d.alert))

    step(ap.OK)                      # 정상
    c.advance(minutes=5); step(ap.OTHER)   # 실패 시작 — 안 알림
    c.advance(minutes=20); step(ap.OTHER)  # 25분 — 아직
    c.advance(minutes=10); step(ap.OTHER)  # 35분 — ★첫 알림★
    c.advance(hours=3); step(ap.OTHER)     # 억제
    c.advance(days=1); step(ap.OTHER)      # ★재알림★
    c.advance(minutes=10); step(ap.OK)     # ★복구 알림★

    assert len(sent) == 3, [s[1] for s in sent]
    assert "연결이" in sent[0][1] and "복구" in sent[2][1]


# ── /status 한 줄 ───────────────────────────────────────────────────────────

def test_status_는_마지막_성공과_백오프를_보여준다():
    line = ap.status_line(T0 + timedelta(hours=3), last_success_at=T0, fail_since=T0 + timedelta(hours=1), backoff_step=4)
    assert "3시간" in line and "백오프 4단계" in line


def test_status_는_기록이_없으면_그렇게_적는다():
    assert "기록 없음" in ap.status_line(T0, last_success_at=None)


# ── 공허한 통과 방지 ─────────────────────────────────────────────────────────

def test_정책이_파일을_건드리지_않는다():
    # ★상태 파일 스키마를 모른다는 것이 이 모듈의 계약이다.★ 입출력이 들어오면 그 계약이 깨진다.
    src = (ROOT / "alert_policy.py").read_text(encoding="utf-8")
    for banned in ("open(", "Path(", "json.", "read_text", "write_text"):
        assert banned not in src, f"alert_policy 가 {banned} 를 쓴다 — 순수해야 한다"


def test_시험이_실제로_알림을_만들어봤다():
    # 단정이 전부 'alert is None' 쪽으로만 몰리면 정책이 아무것도 안 해도 통과한다.
    assert fail(T0 + timedelta(minutes=31), fail_since=T0).alert is not None
