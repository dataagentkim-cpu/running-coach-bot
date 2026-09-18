# 심박존 출처 전환 시험 — ★Garmin 호출 없음.★
#
# 별도 조회(fetch_hr_zones)로 받아 캐시하던 값이 ★활동 페이로드에 이미 실려 온다.★
# 초 단위로 같은 값이고(대조 21/21 일치), 캐시에만 있던 zoneLowBoundary 는 코드가 안 쓴다.
# 다만 보유율이 100%가 아니라(측정 78건 중 76건) ★캐시 폴백이 없으면 그 활동의 존 라인이
# 조용히 사라진다.★ 그 폴백이 실제로 동작하는지가 이 파일의 핵심이다.
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import coach as c  # noqa: E402

ACT_WITH = {
    "activityId": 111,
    "hrTimeInZone_1": 39.0,
    "hrTimeInZone_2": 177.385,
    "hrTimeInZone_3": 300.987,
    "hrTimeInZone_4": 1827.933,
    "hrTimeInZone_5": 304.0,
}
ACT_WITHOUT = {"activityId": 222}
CACHE = {"222": [{"zoneNumber": 4, "secsInZone": 900.0, "zoneLowBoundary": 150}]}


def test_활동에서_다섯_존을_뽑는다():
    z = c._zones_from_activity(ACT_WITH)
    assert [x["zoneNumber"] for x in z] == [1, 2, 3, 4, 5]
    assert z[3]["secsInZone"] == 1827.933  # 초 단위 그대로 — 환산하지 않는다


def test_필드가_없으면_빈_목록():
    assert c._zones_from_activity(ACT_WITHOUT) == []


def test_일부만_있어도_있는_것만_뽑는다():
    z = c._zones_from_activity({"activityId": 1, "hrTimeInZone_4": 100.0})
    assert z == [{"zoneNumber": 4, "secsInZone": 100.0}]


def test_0초_존도_버리지_않는다():
    # 0 은 '없음' 이 아니다. 버리면 총합이 달라져 고강도 비율이 틀린다.
    z = c._zones_from_activity({"activityId": 1, "hrTimeInZone_1": 0.0, "hrTimeInZone_2": 60.0})
    assert len(z) == 2


# ── 폴백 ─────────────────────────────────────────────────────────────────────

def test_활동에_있으면_활동을_쓴다():
    assert c.zones_for(ACT_WITH, {"111": [{"zoneNumber": 9, "secsInZone": 1}]})[0]["zoneNumber"] == 1


def test_활동에_없으면_캐시로_간다():
    # ★보유율이 100%가 아니라서 이 폴백이 필요하다.★ 없으면 그 활동의 존 라인이 사라진다.
    assert c.zones_for(ACT_WITHOUT, CACHE)[0]["secsInZone"] == 900.0


def test_둘_다_없으면_빈_목록():
    assert c.zones_for(ACT_WITHOUT, {}) == []


# ── 기존 소비자가 그대로 동작하는가 ──────────────────────────────────────────

def test_zone_summary_가_활동값으로_동작한다():
    # ★_zone_summary 본문은 안 건드렸다.★ 입력을 만드는 어댑터만 갈아끼웠다.
    out = c._zone_summary(c.zones_for(ACT_WITH, {}))
    assert "Z4" in out and "고강도" in out


def test_plan_vs_actual_이_활동값으로_동작한다():
    spec = {"steps": [{"type": "interval", "condition": {"duration_sec": 600}}]}
    line = c._plan_vs_actual(spec, c.zones_for(ACT_WITH, {}))
    assert "계획 고강도 10분" in line and "실제 Z4+" in line


# ── 제거 확인 ────────────────────────────────────────────────────────────────

def test_별도_조회_경로가_사라졌다():
    # 남아 있으면 새 런마다 호출이 하나 더 나간다 — 줄이려던 그 호출이다.
    import garmin_client as gc
    assert not hasattr(gc.GarminClient, "fetch_hr_zones")
    for f in ("garmin_client.py", "scheduler.py", "coach.py", "bot.py"):
        assert "fetch_hr_zones" not in (ROOT / f).read_text(encoding="utf-8"), f


def test_캐시_읽기는_남아있다():
    # ★폴백이 그것을 쓴다.★ 같이 지우면 보유율 미달 활동이 조용히 빈다.
    import garmin_client as gc
    assert hasattr(gc, "load_hr_zones") and hasattr(gc, "save_hr_zones")


def test_시험이_실제로_두_경로를_다_탔다():
    # 한쪽만 타면 폴백이 죽어 있어도 통과한다.
    assert c.zones_for(ACT_WITH, {}) and c.zones_for(ACT_WITHOUT, CACHE)
