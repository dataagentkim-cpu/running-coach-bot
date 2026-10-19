"""랩 심박 구조. 픽스처는 2026-09-24 인터벌 런의 랩 숫자만 옮긴 것이다(좌표 없음)."""
from hr_structure import compact_laps, hr_structure_lines, planned_reps


def _lap(t, dist, dur, mov, avg, mx):
    return {"intensityType": t, "distance": dist, "duration": dur,
            "movingDuration": mov, "averageHR": avg, "maxHR": mx}


INTERVAL = [
    _lap("WARMUP", 1000, 436, 436, 132, 142),
    _lap("WARMUP", 365, 164, 164, 135, 139),
    _lap("ACTIVE", 400, 101, 101, 145, 156), _lap("RECOVERY", 200, 135, 135, 135, 174),
    _lap("ACTIVE", 400, 185, 116, 135, 159), _lap("RECOVERY", 200, 99, 99, 136, 157),
    _lap("ACTIVE", 400, 155, 111, 149, 168), _lap("RECOVERY", 200, 113, 110, 148, 175),
    _lap("ACTIVE", 400, 209, 138, 126, 158), _lap("RECOVERY", 200, 117, 116, 133, 154),
    _lap("ACTIVE", 400, 114, 114, 161, 175), _lap("RECOVERY", 138, 158, 92, 138, 175),
]
SPEC_8 = {"steps": [{"type": "warmup"},
                    {"type": "repeat", "iterations": 8,
                     "steps": [{"type": "interval"}, {"type": "recovery"}]}]}


def test_planned_reps():
    assert planned_reps(SPEC_8) == 8
    assert planned_reps(None) == 0
    assert planned_reps({"steps": [{"type": "repeat", "iterations": 3, "steps": [{"type": "run"}]}]}) == 0


def test_interval_completion_against_plan():
    lines = hr_structure_lines(INTERVAL, SPEC_8)
    assert lines[0] == "인터벌 5회 / 계획 8회 (미완수)"


def test_peak_takes_recovery_lap_because_hr_lags():
    lines = hr_structure_lines(INTERVAL, SPEC_8)
    peak = next(l for l in lines if l.startswith("반복 피크심박"))
    # 1회차 ACTIVE max 156 이지만 뒤 RECOVERY 에서 174 — 피크는 174
    assert peak.startswith("반복 피크심박: 174 / 159 / 175 / 158 / 175")
    assert "(첫→끝 +1)" in peak


def test_pace_uses_moving_time_and_flags_stops():
    pace = next(l for l in hr_structure_lines(INTERVAL) if l.startswith("반복 페이스"))
    assert pace.startswith("반복 페이스: 4:12 / 4:50* / 4:37* / 5:45* / 4:45")
    assert "(*반복 중 정지)" in pace


def test_recovery_hr_listed():
    rec = next(l for l in hr_structure_lines(INTERVAL) if l.startswith("회복구간"))
    assert rec == "회복구간 평균심박: 135 / 136 / 148 / 133 / 138"


def test_no_plan_no_plan_text():
    assert hr_structure_lines(INTERVAL)[0] == "인터벌 5회"


def test_steady_decoupling_flags_late_drift():
    laps = [_lap("INTERVAL", 1000, 360, 360, hr, hr + 5) for hr in (140, 142, 150, 156)]
    line = hr_structure_lines(laps)[0]
    assert line.startswith("전반 6:00 @ 141bpm → 후반 6:00 @ 153bpm, 디커플링 +8.5%")
    assert "유산소 지속력 한계" in line


def test_steady_even_run_no_warning():
    laps = [_lap("INTERVAL", 1000, 360, 360, 145, 150) for _ in range(6)]
    line = hr_structure_lines(laps)[0]
    assert "디커플링 +0.0%" in line and "한계" not in line


def test_too_few_laps_or_empty_is_silent():
    assert hr_structure_lines([]) == []
    assert hr_structure_lines([_lap("INTERVAL", 1000, 360, 360, 145, 150)] * 3) == []


def test_compact_drops_location():
    raw = {"lapDTOs": [{**INTERVAL[0], "startLatitude": 37.5, "endLongitude": 127.0, "lapIndex": 1}]}
    out = compact_laps(raw)
    assert out == [INTERVAL[0]]
    assert compact_laps({}) == [] and compact_laps(None) == []
