# 정리 선택 로직 · 계획/실제 대조 단위 시험 — ★네트워크·인증 없음★.
# API 를 부르는 래퍼가 아니라 ★무엇을 지울지 고르는 순수 함수★ 를 잰다.
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from garmin_client import workouts_to_cleanup  # noqa: E402

TODAY = date(2026, 9, 18)


def w(name, wid=1):
    return {"workoutName": name, "workoutId": wid}


# ── 정리 대상 고르기 ─────────────────────────────────────────────────────────

def test_오래된_RC_워크아웃만_고른다():
    items = [w("RC-20260901-I400x8", 1), w("RC-20260917-T20min", 2)]
    assert workouts_to_cleanup(items, TODAY, keep_days=14) == [1]


def test_사용자가_만든_워크아웃은_절대_안_고른다():
    # ★RC- 접두사가 소유권 표시다.★ 접두사가 없으면 아무리 오래돼도 남의 것이다.
    items = [w("내 인터벌 20250101", 9), w("Easy Run", 10), w("rc-20250101-x", 11)]
    assert workouts_to_cleanup(items, TODAY, keep_days=14) == []


def test_미래_예약은_안_고른다():
    assert workouts_to_cleanup([w("RC-20261231-I400x8", 3)], TODAY, keep_days=14) == []


def test_날짜를_못_읽으면_안_고른다():
    # 모르는 것은 지우지 않는다 — 잘못 지우면 되돌릴 수 없다.
    items = [w("RC-I400x8", 4), w("RC-2026-09-01-x", 5), w("RC-20261301-x", 6)]
    assert workouts_to_cleanup(items, TODAY, keep_days=14) == []


@pytest.mark.parametrize("made,keep,expected", [
    ("20260904", 14, []),      # 정확히 14일 — 경계는 남긴다
    ("20260903", 14, [7]),     # 15일 — 지운다
    ("20260918", 14, []),      # 오늘
])
def test_보관기간_경계(made, keep, expected):
    assert workouts_to_cleanup([w(f"RC-{made}-I400x8", 7)], TODAY, keep_days=keep) == expected


def test_workoutId_가_없으면_건너뛴다():
    assert workouts_to_cleanup([{"workoutName": "RC-20260101-x"}], TODAY, keep_days=14) == []


# ── 계획 / 실제 대조 ─────────────────────────────────────────────────────────

def _coach():
    # coach 는 import 시 anthropic 키를 읽지 않지만, 클라이언트 생성은 인스턴스화 때다.
    import coach as c
    return c


SPEC = {
    "steps": [
        {"type": "warmup", "condition": {"duration_sec": 600}},
        {"type": "repeat", "iterations": 4, "steps": [
            {"type": "interval", "condition": {"duration_sec": 240}},
            {"type": "recovery", "condition": {"duration_sec": 120}},
        ]},
    ]
}


def test_계획시간은_interval만_반복횟수만큼_센다():
    # 워밍업·회복은 고강도가 아니다. 그걸 같이 세면 대조가 통째로 틀어진다.
    assert _coach()._planned_hard_seconds(SPEC) == 4 * 240


def test_대조라인이_퍼센트를_보여준다():
    zones = [{"zoneNumber": 4, "secsInZone": 1200}, {"zoneNumber": 2, "secsInZone": 900}]
    line = _coach()._plan_vs_actual(SPEC, zones)
    assert "계획 고강도 16분" in line
    assert "실제 Z4+ 20.0분" in line
    assert "+25%" in line


def test_Z4_기록이_없으면_그렇게_적는다():
    line = _coach()._plan_vs_actual(SPEC, [{"zoneNumber": 2, "secsInZone": 1800}])
    assert "기록 없음" in line


@pytest.mark.parametrize("spec,zones", [
    ({}, [{"zoneNumber": 4, "secsInZone": 60}]),
    (SPEC, []),
    ({"steps": [{"type": "warmup", "condition": {"duration_sec": 600}}]}, [{"zoneNumber": 4, "secsInZone": 60}]),
])
def test_셀_수_없으면_빈_문자열(spec, zones):
    # 셀 수 없는데 숫자를 만들어내면 그 줄이 다음 처방의 입력이 되어 거짓이 전파된다.
    assert _coach()._plan_vs_actual(spec, zones) == ""


def test_목표없는_거리구간은_계획시간에서_뺀다():
    spec = {"steps": [{"type": "interval", "condition": {"distance_m": 1000}}]}
    assert _coach()._planned_hard_seconds(spec) == 0


def test_목표페이스가_있으면_거리도_환산한다():
    spec = {"steps": [{"type": "interval", "condition": {"distance_m": 1000},
                       "target": {"pace_range_sec": [280.0, 300.0]}}]}
    assert _coach()._planned_hard_seconds(spec) == pytest.approx(290.0)


# ── 배포 환경 제약 가드 ──────────────────────────────────────────────────────

FORBIDDEN = [
    # 배포 환경(garminconnect 0.3.6)에 ★없는 메서드★ 다. 로컬 판본에 있으면 그대로 돌아서
    # 배포에서만 깨진다 — 그 실패는 사용자 쪽에서야 보인다.
    "push_workout_to_device",
    "get_next_scheduled_workout",
    "create_distance_interval_step",
    # 기기로 직접 밀어넣지 않으므로 기기 조회가 필요 없다. 부르면 호출량만 는다.
    "get_devices",
    "get_primary_training_device",
]


@pytest.mark.parametrize("name", FORBIDDEN)
def test_금지된_호출이_코드에_없다(name):
    root = Path(__file__).resolve().parent.parent
    hits = []
    for f in sorted(root.glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if name in line and not line.lstrip().startswith("#"):
                hits.append(f"{f.name}:{i}")
    assert not hits, f"{name} 사용처: {hits}"


def test_업로드_경로가_쓰는_호출은_둘뿐이다():
    # ★세션 1건당 신규 Garmin 호출 2회(upload + schedule)★ 가 승인된 상한이다.
    import garmin_client as gc
    src = Path(gc.__file__).read_text(encoding="utf-8")
    body = src[src.index("def upload_workout"):src.index("def list_workouts")]
    assert body.count("self.api.") == 2
