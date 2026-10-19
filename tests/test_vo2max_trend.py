"""VO2max 이력 누적과 주간 리포트 추이 줄."""
from datetime import date, datetime

import pytz

import analytics as an

KST = pytz.timezone("Asia/Seoul")


def _act(d, v):
    ts = KST.localize(datetime.strptime(d, "%Y-%m-%d").replace(hour=7)).timestamp() * 1000
    return {"beginTimestamp": ts, "vO2MaxValue": v}


def test_merge_keeps_old_history_and_skips_missing():
    hist = {"2026-07-01": 48.0}
    acts = [_act("2026-09-24", 47), _act("2026-10-15", None), _act("2026-09-05", 46)]
    out = an.merge_vo2max_history(acts, hist)
    assert out == {"2026-07-01": 48.0, "2026-09-05": 46.0, "2026-09-24": 47.0}


def test_trend_line_compares_4_and_12_weeks():
    hist = {"2026-07-25": 47.0, "2026-09-05": 46.0, "2026-09-24": 47.0}
    line = an.vo2max_trend_line(hist, today=date(2026, 10, 19))
    assert line.split("\n")[0] == "VO2max 47 (9/24 기준) | 4주 전 46 (+1) | 12주 전 47 (±0)"


def test_stale_note_after_two_weeks():
    line = an.vo2max_trend_line({"2026-09-24": 47.0}, today=date(2026, 10, 19))
    assert "3주째 갱신 없음" in line


def test_empty_history_is_silent():
    assert an.vo2max_trend_line({}) == ""
