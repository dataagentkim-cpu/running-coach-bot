# 자동 알림 스케줄러 — 런 감지·아침 레디니스·주간 리포트
from __future__ import annotations

import logging
import os
import urllib.request
import json as _json
import pytz
from datetime import datetime, timedelta

from telegram.ext import CallbackContext

KST = pytz.timezone("Asia/Seoul")

# 여의도 좌표
_YEOUIDO_LAT = 37.5218
_YEOUIDO_LON = 126.9245


def _fetch_weather() -> str:
    """Open-Meteo로 여의도 현재 날씨 조회 (API 키 불필요)."""
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={_YEOUIDO_LAT}&longitude={_YEOUIDO_LON}"
        f"&current=temperature_2m,apparent_temperature,weathercode,windspeed_10m,precipitation"
        f"&timezone=Asia%2FSeoul"
    )
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        cur = data["current"]
        temp = cur.get("temperature_2m", "?")
        feels = cur.get("apparent_temperature", "?")
        wind = cur.get("windspeed_10m", "?")
        rain = cur.get("precipitation", 0)
        code = cur.get("weathercode", 0)

        # WMO 날씨 코드 → 한국어 요약
        if code == 0:
            desc = "맑음 ☀️"
        elif code in (1, 2):
            desc = "구름 조금 🌤"
        elif code == 3:
            desc = "흐림 ☁️"
        elif code in range(51, 68):
            desc = "비 🌧"
        elif code in range(71, 78):
            desc = "눈 🌨"
        elif code in range(80, 83):
            desc = "소나기 🌦"
        elif code in range(95, 100):
            desc = "뇌우 ⛈"
        else:
            desc = f"코드{code}"

        rain_str = f" | 강수 {rain}mm" if rain > 0 else ""
        return f"🌡 여의도 날씨: {desc} {temp}°C (체감 {feels}°C) | 바람 {wind}km/h{rain_str}"
    except Exception as e:
        log.warning("날씨 조회 실패: %s", e)
        return ""

import analytics as an
from coach import RunningCoach
from garmin_client import (
    GarminClient,
    load_activities,
    load_seen_ids,
    load_wellness,
    save_seen_ids,
    _save,
)
from pathlib import Path

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

_coach = RunningCoach()


def _chat_id() -> int:
    return int(os.getenv("RUNCOACH_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "0"))


async def _send(context: CallbackContext, text: str) -> None:
    await context.bot.send_message(chat_id=_chat_id(), text=text)


# ── ① 런 후 자동 분석 (5분마다) ─────────────────────────────────────────────

async def check_new_runs(context: CallbackContext) -> None:
    """새 러닝 활동 감지 → 자동 분석 메시지 발송."""
    try:
        client = GarminClient()
        recent = client.quick_check(limit=5)
    except Exception as e:
        log.warning("Garmin 폴링 실패: %s", e)
        return

    running = [
        a for a in recent
        if a.get("activityType", {}).get("typeKey", "").startswith("running")
        or a.get("activityType", {}).get("typeKey", "") in ("running", "track_running", "treadmill_running", "trail_running")
    ]
    if not running:
        return

    seen = load_seen_ids()  # 디스크에 저장된 이전 seen 로드

    # 48시간 이내 런이면서 이전에 본 적 없는 것만 "새 런" — 재배포 후 초기화돼도 오래된 런은 무시
    cutoff_ts = (datetime.now(KST) - timedelta(hours=48)).timestamp() * 1000
    new_runs = [
        a for a in running
        if a.get("activityId")
        and a["activityId"] not in seen
        and (a.get("beginTimestamp") or 0) >= cutoff_ts
    ]

    # 최근 5개 활동 ID를 모두 seen에 등록 (재배포 후에도 중복 알림 방지)
    for a in running:
        if a.get("activityId"):
            seen.add(a["activityId"])
    save_seen_ids(seen)

    if not new_runs:
        return

    # 캐시 업데이트
    cached = load_activities()
    cached_ids = {a.get("activityId") for a in cached}
    for run in new_runs:
        if run.get("activityId") not in cached_ids:
            cached.insert(0, run)
    _save(DATA_DIR / "activities.json", cached)

    wellness = load_wellness()

    for run in new_runs:
        dist_km = (run.get("distance") or 0) / 1000
        dur_sec = run.get("duration") or run.get("movingDuration") or 0
        avg_hr = run.get("averageHR") or 0
        pace_sec = dur_sec / dist_km if dist_km else 0

        # 런 실제 날짜·요일 계산 (KST 기준)
        run_ts = run.get("beginTimestamp") or 0
        run_dt = datetime.fromtimestamp(run_ts / 1000, tz=KST) if run_ts else datetime.now(KST)
        run_date_str = run_dt.strftime("%m/%d")
        run_wd = ["월", "화", "수", "목", "금", "토", "일"][run_dt.weekday()]
        hours_ago = (datetime.now(KST).timestamp() - run_dt.timestamp()) / 3600
        time_label = "방금" if hours_ago < 3 else f"{run_date_str}({run_wd})"

        header = (
            f"🏃 런 완료! 자동 분석\n\n"
            f"{run.get('activityName', '러닝')} — {dist_km:.2f}km @ {an.format_pace(pace_sec)}\n"
            f"심박 {round(avg_hr)}bpm | 시간 {round(dur_sec/60)}분\n\n"
        )

        try:
            analysis = _coach.chat(
                f"{time_label} {run_date_str}({run_wd}) {dist_km:.1f}km 런을 {an.format_pace(pace_sec)} 페이스로 완료했어. "
                f"평균심박 {round(avg_hr)}bpm. 이 런을 짧게 분석해주고 다음 훈련 한 줄만 처방해줘.",
                cached,
                wellness,
            )
            await _send(context, header + analysis)
        except Exception as e:
            await _send(context, header + f"분석 오류: {e}")


# ── ② 아침 레디니스 알림 (매일 07:00 KST) ────────────────────────────────────

async def morning_readiness(context: CallbackContext) -> None:
    """바디배터리·수면 기반 오늘 훈련 강도 제안."""
    try:
        client = GarminClient()
        today_wellness = client.sync_wellness_today()
    except Exception as e:
        log.warning("웰니스 조회 실패: %s", e)
        return

    # 어제 데이터 우선 (오늘 아침엔 어제 밤 데이터가 가장 신선) — KST 기준
    now_kst = datetime.now(KST)
    yesterday = (now_kst - timedelta(days=1)).strftime("%Y-%m-%d")
    today = now_kst.strftime("%Y-%m-%d")
    w = today_wellness.get(yesterday) or today_wellness.get(today) or {}

    bb = (
        w.get("bodyBatteryMostRecentValue")
        or w.get("bodyBatteryHighestValue")
        or w.get("highestBodyBattery")
    )
    sleep_h = (w.get("sleepingSeconds") or 0) / 3600
    stress = w.get("averageStressLevel") or 0
    rhr = w.get("restingHeartRate") or 0

    score, status, rec = an.readiness_from_wellness(w)

    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]
    date_str = now_kst.strftime(f"%m월 %d일 ({weekday_kr}요일)")
    lines = [f"🌅 굿모닝! {date_str} 레디니스 체크\n"]

    weather = _fetch_weather()
    if weather:
        lines.append(weather)
        lines.append("")

    if bb:
        lines.append(f"⚡ 바디배터리: {bb}/100")
    if sleep_h > 0:
        lines.append(f"💤 수면: {sleep_h:.1f}h")
    if stress:
        lines.append(f"😤 어제 스트레스: {stress}/100")
    if rhr:
        lines.append(f"❤️ 안정 심박: {rhr}bpm")

    lines.append(f"\n{status}")
    lines.append(f"→ {rec}")

    # 현재 피로(TSB) 추가
    acts = load_activities()
    if acts:
        load_series = an.daily_load_series(acts, days=90)
        ctl, atl, tsb = an.ctl_atl_tsb(load_series)
        lines.append(f"\n컨디션(TSB): {tsb:+.0f} | 체력(CTL): {ctl:.0f} | 피로(ATL): {atl:.0f}")

    lines.append("\n/today 로 오늘 워크아웃 받아봐 💪")
    await _send(context, "\n".join(lines))


# ── ③ 주간 리포트 (매주 월요일 09:00 KST) ────────────────────────────────────

async def weekly_report(context: CallbackContext) -> None:
    """지난 주 요약 + 이번 주 훈련 계획 발송."""
    try:
        client = GarminClient()
        client.sync(weeks=10)
    except Exception as e:
        log.warning("주간 리포트 sync 실패: %s", e)

    acts = load_activities()
    wellness = load_wellness()
    if not acts:
        return

    # 지난 주 요약
    weekly = an.weekly_summary(acts, weeks=2)
    last_week = weekly[1] if len(weekly) > 1 else weekly[0] if weekly else {}
    this_week = weekly[0] if weekly else {}

    vdot = an.best_vdot_from_activities(acts)
    load_series = an.daily_load_series(acts, days=90)
    ctl, atl, tsb = an.ctl_atl_tsb(load_series)

    header = "📊 주간 러닝 리포트\n\n"

    last_str = ""
    if last_week:
        last_str = (
            f"📅 지난 주 ({last_week['week']})\n"
            f"  {last_week['runs']}회 | {last_week['total_km']}km | {last_week['total_min']}분\n"
            f"  평균 심박: {last_week['avg_hr']}bpm\n\n"
        )

    fitness_str = (
        f"📈 현재 피트니스\n"
        f"  VDOT {vdot:.1f} | CTL {ctl:.0f} | TSB {tsb:+.0f}\n\n"
    )

    try:
        plan = _coach.weekly_plan(acts, wellness)
        await _send(context, header + last_str + fitness_str + "🗓 이번 주 계획\n" + plan)
    except Exception as e:
        await _send(context, header + last_str + fitness_str + f"계획 생성 오류: {e}")
