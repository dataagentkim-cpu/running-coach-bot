# 게이트·알림 배선 시험 — ★Garmin 호출 없음. 상태 파일은 임시 경로로 돌린다.★
#
# 여기서 재는 것은 판단이 아니라 ★배선★ 이다. 정책은 순수 모듈이 이미 시험한다.
# 배선에서 틀리는 것은 "키 이름이 안 맞아 조용히 아무것도 안 하는" 종류라 따로 잡아야 한다.
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import alert_policy as ap  # noqa: E402
import garmin_client as gc  # noqa: E402

T0 = datetime(2026, 9, 18, 9, 0, 0)


@pytest.fixture
def state(tmp_path, monkeypatch):
    """상태 파일을 임시 경로로 돌린다 — ★실제 data/ 를 건드리지 않는다.★"""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(gc, "DATA_DIR", d)
    monkeypatch.setattr(gc, "_STATE_PATH", d / "garmin_state.json")
    monkeypatch.setattr(gc, "_DISABLED_PATH", d / "garmin_disabled")
    return d / "garmin_state.json"


# ── 기존 파일에 새 키가 없어도 살아야 한다 ───────────────────────────────────

def test_기존_3키만_있는_파일도_읽힌다(state):
    # ★EC2 에 이미 이 파일이 있고 새 키가 없다.★ KeyError 가 나면 알림이 통째로 안 돈다.
    state.write_text(json.dumps({"blocked_until": 0.0, "fail_count": 2, "last_relogin": 123.0}))
    st = gc._load_gate()
    assert st["fail_count"] == 2                 # 기존 값은 유지
    assert st["last_success_at"] is None         # 새 키는 기본값으로 채워진다
    assert st["last_notified_at"] is None


def test_파일이_깨져도_기본값으로_산다(state):
    state.write_text("{이건 JSON 이 아니다")
    assert gc._load_gate()["fail_count"] == 0


def test_파일이_리스트여도_기본값으로_산다(state):
    state.write_text("[1,2,3]")
    assert gc._load_gate()["blocked_until"] == 0.0


# ── 기록이 기존 키를 보존하는가 ──────────────────────────────────────────────

def test_결과기록이_게이트_키를_안_건드린다(state):
    # ★게이트는 다른 곳의 정본이다.★ 알림이 그 값을 덮으면 백오프가 풀린다.
    state.write_text(json.dumps({"blocked_until": 999.0, "fail_count": 3, "last_relogin": 5.0}))
    gc.record_garmin_result(ap.OTHER, now=T0)
    st = json.loads(state.read_text())
    assert st["blocked_until"] == 999.0
    assert st["fail_count"] == 3
    assert st["last_relogin"] == 5.0


def test_성공이_마지막_성공시각을_적는다(state):
    gc.record_garmin_result(ap.OK, now=T0)
    assert json.loads(state.read_text())["last_success_at"] == T0.isoformat()


def test_실패가_시작시각을_적고_바로는_안_알린다(state):
    alert = gc.record_garmin_result(ap.OTHER, now=T0)
    assert alert is None
    assert json.loads(state.read_text())["fail_since"] == T0.isoformat()


def test_30분_지나면_알림이_나온다(state):
    gc.record_garmin_result(ap.OTHER, now=T0)
    alert = gc.record_garmin_result(ap.OTHER, now=T0 + timedelta(minutes=31))
    assert alert is not None


def test_auth_는_즉시_알린다(state):
    assert gc.record_garmin_result(ap.AUTH, now=T0) is not None


def test_복구_알림_뒤_상태가_비워진다(state):
    gc.record_garmin_result(ap.OTHER, now=T0)
    gc.record_garmin_result(ap.OTHER, now=T0 + timedelta(minutes=31))
    alert = gc.record_garmin_result(ap.OK, now=T0 + timedelta(hours=1))
    assert alert and "복구" in alert
    st = json.loads(state.read_text())
    assert st["fail_since"] is None and st["last_notified_at"] is None


def test_clear_gate_가_마지막_성공시각을_지우지_않는다(state):
    # 게이트 해제는 게이트 얘기다. 마지막 성공 시각은 그와 무관한 사실이다.
    gc.record_garmin_result(ap.OK, now=T0)
    gc.clear_garmin_gate()
    assert json.loads(state.read_text())["last_success_at"] == T0.isoformat()


# ── /status 한 줄 ───────────────────────────────────────────────────────────

def test_status_줄에_마지막_성공이_들어간다(state):
    gc.record_garmin_result(ap.OK, now=T0)
    assert "마지막 성공" in gc.garmin_status_line(now=T0 + timedelta(hours=2))


def test_킬스위치가_status_줄에_보인다(state, tmp_path):
    (state.parent / "garmin_disabled").write_text("stop")
    assert "킬스위치" in gc.garmin_status_line(now=T0)


# ── 공허한 통과 방지 ─────────────────────────────────────────────────────────

def test_픽스처가_실제_data_를_안_쓴다(state):
    # 픽스처가 안 먹으면 이 파일의 시험들이 ★실제 개인 상태 파일을 덮어쓴다.★
    real = ROOT / "data" / "garmin_state.json"
    assert gc._STATE_PATH != real
    assert not str(gc._STATE_PATH).startswith(str(ROOT / "data"))
    assert gc._STATE_PATH == state  # 실제로 이 시험이 쓰는 경로가 픽스처 것이다


def test_알림이_실제로_한번은_생성된다(state):
    gc.record_garmin_result(ap.OTHER, now=T0)
    assert gc.record_garmin_result(ap.OTHER, now=T0 + timedelta(hours=1)) is not None
