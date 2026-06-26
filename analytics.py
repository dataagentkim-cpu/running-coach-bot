# VDOT·CTL/ATL/TSB·페이스존 계산 — Jack Daniels & Banister 공식 기반
from __future__ import annotations

import math
from datetime import datetime, timedelta


# ── VDOT 관련 ────────────────────────────────────────────────────────────────

def vdot_from_vo2max(garmin_vo2max: float) -> float:
    """Garmin VO2max ≈ VDOT (동일 단위, 직접 사용)."""
    return garmin_vo2max


def vdot_from_race(distance_m: float, duration_sec: float) -> float:
    """실제 레이스 기록으로 VDOT 계산 (Jack Daniels 공식)."""
    t = duration_sec / 60.0  # 분
    v = distance_m / t        # m/min

    # VO2 at race pace
    percent_vo2max = (
        0.8 + 0.1894393 * math.exp(-0.012778 * t)
        + 0.2989558 * math.exp(-0.1932605 * t)
    )
    vo2_at_pace = -4.60 + 0.182258 * v + 0.000104 * v ** 2
    return vo2_at_pace / percent_vo2max


def best_vdot_from_activities(activities: list[dict]) -> float | None:
    """활동 목록에서 Garmin VO2max 최신값 또는 레이스 추정 VDOT 반환."""
    # Garmin이 VO2max를 줬으면 그걸 씀
    for act in sorted(activities, key=lambda a: a.get("beginTimestamp", 0), reverse=True):
        vo2 = act.get("vO2MaxValue") or act.get("maxVO2") or act.get("vo2MaxValue")
        if vo2 and float(vo2) > 20:
            return float(vo2)

    # 없으면 최근 레이스 기반 추정 (완주 데이터만)
    candidates = []
    for act in activities:
        dist = act.get("distance", 0)
        dur = act.get("duration", 0) or act.get("movingDuration", 0)
        if dist >= 3000 and dur > 0:
            vdot = vdot_from_race(dist, dur)
            candidates.append(vdot)
    return max(candidates) if candidates else None


def pace_zones(vdot: float) -> dict[str, tuple[float, float]]:
    """
    VDOT로 Daniels 훈련 페이스존 계산.
    반환: {zone_name: (min_sec_per_km, max_sec_per_km)}
    """
    def speed_at_pct(pct: float) -> float:
        """%VO2max에서의 속도 (m/min), 이차방정식 역산."""
        target_vo2 = pct * vdot
        # 0.000104v² + 0.182258v - (4.60 + target_vo2) = 0
        a, b, c = 0.000104, 0.182258, -(4.60 + target_vo2)
        return (-b + math.sqrt(b ** 2 - 4 * a * c)) / (2 * a)

    def to_sec_per_km(speed_m_per_min: float) -> float:
        return 1000 / speed_m_per_min * 60

    zones_pct = {
        "E (Easy)":       (0.59, 0.74),
        "M (Marathon)":   (0.75, 0.84),
        "T (Threshold)":  (0.83, 0.88),
        "I (Interval)":   (0.95, 1.00),
        "R (Repetition)": (1.05, 1.15),
    }
    return {
        name: (to_sec_per_km(speed_at_pct(hi)), to_sec_per_km(speed_at_pct(lo)))
        for name, (lo, hi) in zones_pct.items()
    }


def format_pace(sec_per_km: float) -> str:
    """초/km → '5:32/km' 형식."""
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


# ── 훈련 부하 (PMC) ─────────────────────────────────────────────────────────

def daily_load_series(activities: list[dict], days: int = 90) -> list[float]:
    """
    날짜별 일일 훈련 부하 배열 반환 (인덱스 0 = days일 전, -1 = 오늘).
    Garmin activityTrainingLoad 우선, 없으면 TRIMP 추정.
    """
    today = datetime.now().date()
    load_map: dict[int, float] = {}  # days_ago → load

    for act in activities:
        ts = act.get("beginTimestamp") or act.get("startTimeGMT")
        if not ts:
            continue
        try:
            act_date = datetime.fromtimestamp(int(ts) / 1000).date() if isinstance(ts, (int, float)) else datetime.fromisoformat(str(ts).replace("Z", "")).date()
        except Exception:
            continue
        days_ago = (today - act_date).days
        if days_ago < 0 or days_ago >= days:
            continue

        load = (
            act.get("activityTrainingLoad")
            or act.get("trainingLoad")
            or _trimp_estimate(act)
        )
        if load:
            load_map[days_ago] = load_map.get(days_ago, 0.0) + float(load)

    return [load_map.get(days - 1 - i, 0.0) for i in range(days)]


def _trimp_estimate(act: dict) -> float:
    """HR 데이터 없을 때 거리·페이스 기반 TRIMP 추정."""
    dist_km = (act.get("distance") or 0) / 1000
    dur_min = (act.get("duration") or act.get("movingDuration") or 0) / 60
    avg_hr = act.get("averageHR") or 140
    # 단순 TRIMP: duration * HR ratio (max HR 190 기준)
    hr_ratio = (avg_hr - 60) / (190 - 60)
    return dur_min * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)


def ctl_atl_tsb(load_series: list[float]) -> tuple[float, float, float]:
    """
    Banister PMC: CTL(체력), ATL(피로), TSB(컨디션) 계산.
    load_series: 오래된 날 → 최근 날 순서.
    """
    decay_ctl = math.exp(-1 / 42)
    decay_atl = math.exp(-1 / 7)
    ctl = atl = 0.0
    for load in load_series:
        ctl = ctl * decay_ctl + load * (1 - decay_ctl)
        atl = atl * decay_atl + load * (1 - decay_atl)
    tsb = ctl - atl
    return round(ctl, 1), round(atl, 1), round(tsb, 1)


# ── 주간 요약 ───────────────────────────────────────────────────────────────

def weekly_summary(activities: list[dict], weeks: int = 4) -> list[dict]:
    """최근 N주 주간 통계 반환."""
    today = datetime.now().date()
    result = []
    for w in range(weeks):
        week_start = today - timedelta(days=today.weekday() + 7 * w)
        week_end = week_start + timedelta(days=6)
        week_acts = []
        for act in activities:
            ts = act.get("beginTimestamp") or act.get("startTimeGMT")
            if not ts:
                continue
            try:
                act_date = (
                    datetime.fromtimestamp(int(ts) / 1000).date()
                    if isinstance(ts, (int, float))
                    else datetime.fromisoformat(str(ts).replace("Z", "")).date()
                )
            except Exception:
                continue
            if week_start <= act_date <= week_end:
                week_acts.append(act)

        if not week_acts and w > 0:
            continue

        total_km = sum((a.get("distance") or 0) for a in week_acts) / 1000
        total_min = sum((a.get("duration") or 0) for a in week_acts) / 60
        runs = len(week_acts)
        avg_hr = (
            sum((a.get("averageHR") or 0) for a in week_acts) / runs
            if runs else 0
        )
        result.append({
            "week": f"{week_start.strftime('%m/%d')}~{week_end.strftime('%m/%d')}",
            "runs": runs,
            "total_km": round(total_km, 1),
            "total_min": round(total_min),
            "avg_hr": round(avg_hr),
        })
    return result


def readiness_from_wellness(wellness_today: dict) -> tuple[int, str, str]:
    """
    바디배터리 + 수면 기반 오늘 레디니스 판단.
    반환: (점수 0-100, 이모지+한줄평, 훈련강도 추천)
    """
    bb = (
        wellness_today.get("bodyBatteryMostRecentValue")
        or wellness_today.get("bodyBatteryHighestValue")
        or wellness_today.get("highestBodyBattery")
        or 0
    )
    sleep_h = (wellness_today.get("sleepingSeconds") or 0) / 3600
    stress = wellness_today.get("averageStressLevel") or 0
    rhr = wellness_today.get("restingHeartRate") or 0

    score = 50  # 기본값
    if bb:
        score = bb  # 바디배터리가 가장 직접적인 지표
    if sleep_h > 0:
        # 수면 7h 이상이면 보너스, 6h 미만이면 페널티
        sleep_adj = (sleep_h - 7) * 5
        score = max(0, min(100, score + sleep_adj))
    if stress > 60:
        score = max(0, score - 10)

    if score >= 75:
        status = "⚡ 컨디션 좋음"
        rec = "T페이스 or 인터벌 가능"
    elif score >= 50:
        status = "✅ 보통"
        rec = "E페이스 런 추천, T런 가능"
    elif score >= 30:
        status = "⚠️ 다소 피로"
        rec = "가벼운 E런 or 30분 이하 권장"
    else:
        status = "🔴 피로 누적"
        rec = "휴식 또는 10분 이내 가벼운 워크"

    return int(score), status, rec


def last_run_summary(activities: list[dict]) -> dict | None:
    """가장 최근 러닝 활동 요약."""
    if not activities:
        return None
    act = max(
        activities,
        key=lambda a: a.get("beginTimestamp") or a.get("startTimeGMT") or 0,
    )
    dist_km = (act.get("distance") or 0) / 1000
    dur_sec = act.get("duration") or act.get("movingDuration") or 0
    avg_hr = act.get("averageHR") or 0
    pace_sec = dur_sec / dist_km if dist_km else 0

    ts = act.get("beginTimestamp") or act.get("startTimeGMT")
    try:
        act_date = (
            datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
            if isinstance(ts, (int, float))
            else str(ts)[:10]
        )
    except Exception:
        act_date = "?"

    return {
        "date": act_date,
        "name": act.get("activityName", "러닝"),
        "distance_km": round(dist_km, 2),
        "duration_min": round(dur_sec / 60, 1),
        "avg_pace": format_pace(pace_sec) if pace_sec else "?",
        "avg_hr": round(avg_hr),
        "training_load": act.get("activityTrainingLoad") or act.get("trainingLoad"),
        "aerobic_te": act.get("aerobicTrainingEffect"),
    }
