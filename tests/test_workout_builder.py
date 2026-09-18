# workout_builder 단위 시험 — ★네트워크·인증 없음★.
# 조립 결과를 dict 로 덤프해서 Garmin 이 받는 모양 그대로 확인한다.
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import workout_builder as wb  # noqa: E402

# analytics.pace_zones 가 내는 것과 같은 모양. (빠른 초/km, 느린 초/km)
PACE_ZONES = {
    "E (Easy)": (330.0, 390.0),
    "M (Marathon)": (300.0, 320.0),
    "T (Threshold)": (280.0, 295.0),
    "I (Interval)": (255.0, 270.0),
    "R (Repetition)": (235.0, 250.0),
}

SPEC_400x8 = {
    "date": "2026-09-18",
    "steps": [
        {"type": "warmup", "condition": {"distance_m": 1000}, "target": {"hr_zone": 2}},
        {"type": "repeat", "iterations": 8, "steps": [
            {"type": "interval", "condition": {"distance_m": 400}, "target": {"pace_zone": "R"}},
            {"type": "recovery", "condition": {"distance_m": 200}, "target": {"hr_zone": 2}},
        ]},
        {"type": "cooldown", "condition": {"distance_m": 1000}, "target": {"hr_zone": 2}},
    ],
}


def steps_of(workout):
    return workout.model_dump()["workoutSegments"][0]["workoutSteps"]


# ── 조립 ─────────────────────────────────────────────────────────────────────

def test_거리_기반_워밍업이_거리로_끝난다():
    # 라이브러리의 create_warmup_step 은 시간 전용이다. 거리 워밍업을 표현할 수 있어야 한다.
    w = wb.build(SPEC_400x8, PACE_ZONES)
    warmup = steps_of(w)[0]
    assert warmup["stepType"]["stepTypeKey"] == "warmup"
    assert warmup["endCondition"]["conditionTypeKey"] == "distance"
    assert warmup["endConditionValue"] == 1000.0


def test_반복_그룹이_횟수와_하위스텝을_싣는다():
    group = steps_of(wb.build(SPEC_400x8, PACE_ZONES))[1]
    assert group["numberOfIterations"] == 8
    assert [s["stepType"]["stepTypeKey"] for s in group["workoutSteps"]] == ["interval", "recovery"]


def test_stepOrder_가_중복없이_증가한다():
    # 순서가 겹치면 시계에서 구간이 뒤섞인다.
    def collect(steps):
        for s in steps:
            yield s["stepOrder"]
            if s.get("workoutSteps"):
                yield from collect(s["workoutSteps"])
    orders = list(collect(steps_of(wb.build(SPEC_400x8, PACE_ZONES))))
    assert orders == sorted(orders)
    assert len(orders) == len(set(orders))


# ── 목표 타입 ────────────────────────────────────────────────────────────────

def test_페이스존이_m_per_s_로_변환된다():
    # ★Garmin 은 m/s 로 받는다★ — 초/km 를 그대로 넣으면 말도 안 되는 페이스가 올라간다.
    interval = steps_of(wb.build(SPEC_400x8, PACE_ZONES))[1]["workoutSteps"][0]
    assert interval["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert interval["targetValueOne"] == pytest.approx(1000.0 / 250.0)
    assert interval["targetValueTwo"] == pytest.approx(1000.0 / 235.0)
    assert interval["targetValueOne"] < interval["targetValueTwo"]


def test_심박은_존번호만_넘긴다():
    # 경계 bpm 은 계정 설정값이다. 우리가 계산하면 계정 설정과 어긋난다.
    recovery = steps_of(wb.build(SPEC_400x8, PACE_ZONES))[1]["workoutSteps"][1]
    assert recovery["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert recovery["zoneNumber"] == 2
    assert "targetValueOne" not in recovery


@pytest.mark.parametrize("dist,expected", [(400, "pace.zone"), (599, "pace.zone"), (600, "heart.rate.zone"), (1000, "heart.rate.zone")])
def test_목표없는_질주는_구간길이로_타입이_갈린다(dist, expected):
    spec = {"date": "2026-09-18", "steps": [{"type": "interval", "condition": {"distance_m": dist}}]}
    assert steps_of(wb.build(spec, PACE_ZONES))[0]["targetType"]["workoutTargetTypeKey"] == expected


@pytest.mark.parametrize("secs,expected", [(90, "pace.zone"), (119, "pace.zone"), (120, "heart.rate.zone")])
def test_시간_기반_질주도_같은_경계를_쓴다(secs, expected):
    spec = {"date": "2026-09-18", "steps": [{"type": "interval", "condition": {"duration_sec": secs}}]}
    assert steps_of(wb.build(spec, PACE_ZONES))[0]["targetType"]["workoutTargetTypeKey"] == expected


def test_회복은_목표를_안적어도_Z2다():
    spec = {"date": "2026-09-18", "steps": [{"type": "recovery", "condition": {"distance_m": 200}}]}
    step = steps_of(wb.build(spec, PACE_ZONES))[0]
    assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert step["zoneNumber"] == wb.EASY_HR_ZONE


# ── 거절해야 하는 입력 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", [
    {"steps": []},
    {"steps": [{"type": "interval"}]},
    {"steps": [{"type": "interval", "condition": {"distance_m": 0}}]},
    {"steps": [{"type": "sprint", "condition": {"distance_m": 400}}]},
    {"steps": [{"type": "repeat", "iterations": 0, "steps": [{"type": "interval", "condition": {"distance_m": 400}}]}]},
    {"steps": [{"type": "repeat", "iterations": 3, "steps": []}]},
])
def test_조립불가_스펙은_SpecError(spec):
    # 조용히 이상한 워크아웃을 만들면 그게 그대로 시계로 올라간다.
    with pytest.raises(wb.SpecError):
        wb.build(spec, PACE_ZONES)


def test_pace_zones_없이_존이름을_쓰면_거절한다():
    spec = {"steps": [{"type": "interval", "condition": {"distance_m": 400}, "target": {"pace_zone": "R"}}]}
    with pytest.raises(wb.SpecError):
        wb.build(spec, None)


# ── 이름 규칙 ────────────────────────────────────────────────────────────────

def test_이름은_RC_접두사와_약칭을_쓴다():
    # RC- 접두사가 곧 소유권 표시다 — 정리가 이걸로만 고른다.
    assert wb.name_for(SPEC_400x8, "2026-09-18") == "RC-20260918-I400x8"


def test_단일_템포는_T약칭():
    spec = {"steps": [{"type": "interval", "condition": {"duration_sec": 1200}}]}
    assert wb.name_for(spec, "2026-09-18") == "RC-20260918-T20min"


def test_build_가_이름을_워크아웃에_싣는다():
    assert wb.build(SPEC_400x8, PACE_ZONES).workoutName == "RC-20260918-I400x8"


# ── 파서 ─────────────────────────────────────────────────────────────────────

def test_기본형을_읽는다():
    spec = wb.parse_interval_text("400m x8 @R 200m 회복", "2026-09-18")
    group = spec["steps"][1]
    assert group["iterations"] == 8
    assert group["steps"][0]["condition"]["distance_m"] == 400
    assert group["steps"][0]["target"]["pace_zone"] == "R"
    assert group["steps"][1]["condition"]["distance_m"] == 200
    assert spec["name"] == "RC-20260918-I400x8"


def test_순서가_바뀌어도_읽는다():
    assert wb.parse_interval_text("8x400m @I 90초 회복", "2026-09-18")["steps"][1]["iterations"] == 8


def test_분_단위_반복도_읽는다():
    spec = wb.parse_interval_text("3분 x5 @T 2분 회복", "2026-09-18")
    group = spec["steps"][1]
    assert group["steps"][0]["condition"]["duration_sec"] == 180
    assert group["steps"][1]["condition"]["duration_sec"] == 120


@pytest.mark.parametrize("text", ["", "   ", "오늘 뭐 뛸까", "400m", "적당히 빡세게"])
def test_못읽으면_None을_준다(text):
    # ★억지로 추측하면 의도하지 않은 세션이 시계로 올라간다.★ 호출부가 자유대화로 넘긴다.
    assert wb.parse_interval_text(text, "2026-09-18") is None


def test_파싱결과가_그대로_조립된다():
    # 파서와 조립기의 계약이 실제로 맞물리는지 — 두 쪽을 따로 고치다 어긋나는 걸 잡는다.
    spec = wb.parse_interval_text("400m x8 @R 200m 회복", "2026-09-18")
    steps = steps_of(wb.build(spec, PACE_ZONES))
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]


# ── tool 정의 ────────────────────────────────────────────────────────────────

def test_tool_스키마가_anthropic_모양이다():
    t = wb.WORKOUT_SPEC_TOOL
    assert t["name"] and t["description"]
    assert t["input_schema"]["required"] == ["date", "steps"]


def test_추정시간이_0보다_크다():
    # estimatedDurationInSecs 는 Garmin 필수 필드다. 0이면 시계 표시가 깨진다.
    assert wb.build(SPEC_400x8, PACE_ZONES).estimatedDurationInSecs > 0
