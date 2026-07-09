# 텔레그램 봇 — 대화형 러닝 코칭 인터페이스 (python-telegram-bot v20+)
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import analytics as an
from coach import RunningCoach, add_coaching_note, load_memory, save_memory, set_goal
from garmin_client import GarminClient, last_sync_time, load_activities, load_wellness

load_dotenv()
log = logging.getLogger(__name__)

coach = RunningCoach()


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _activities_or_warn() -> tuple[list, list, str | None]:
    """캐시된 활동/웰니스 로드. 데이터 없으면 경고 문자 반환."""
    acts = load_activities()
    well = load_wellness()
    if not acts:
        return [], [], "📡 데이터가 없어. /sync 로 Garmin 데이터를 먼저 동기화해줘."
    return acts, well, None


def _sync_status() -> str:
    t = last_sync_time()
    if not t:
        return "❌ 아직 동기화 안 됨"
    from datetime import datetime
    delta = datetime.now() - t
    h = int(delta.total_seconds() // 3600)
    return f"✅ 마지막 동기화: {h}시간 전" if h < 24 else f"⚠️ {t.strftime('%m/%d')} ({h//24}일 전)"


# ── 커맨드 핸들러 ──────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    mem = load_memory()
    goal = mem.get("goal")
    goal_str = f"🎯 목표 레이스: {goal['race']} {goal['distance']} ({goal['date']})" if goal else "목표 레이스 없음 — /goal 로 설정해줘."
    text = (
        "👟 *러닝 코치봇*에 오신 걸 환영해요!\n\n"
        f"{goal_str}\n"
        f"{_sync_status()}\n\n"
        "**명령어:**\n"
        "/sync — Garmin 데이터 동기화\n"
        "/status — 피트니스 현황 리포트\n"
        "/today — 오늘 훈련 처방\n"
        "/last — 마지막 러닝 분석\n"
        "/week — 이번 주 훈련 계획\n"
        "/zones — 페이스존 표\n"
        "/goal — 목표 레이스 설정\n"
        "/note — 코칭 메모 추가\n\n"
        "또는 자유롭게 말을 걸어봐! 💬"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("📡 Garmin Connect 동기화 중...")
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        client = GarminClient()
        acts, well = client.sync(weeks=10)
        await update.message.reply_text(
            f"✅ 동기화 완료!\n"
            f"  • 러닝 활동: {acts}개\n"
            f"  • 웰니스 데이터: {well}일\n\n"
            "이제 /status 로 피트니스 현황을 확인해봐."
        )
    except ValueError as e:
        await update.message.reply_text(
            f"❌ 인증 오류: {e}\n\n"
            ".env 파일에 GARMIN_EMAIL / GARMIN_PASSWORD를 설정해줘."
        )
    except Exception as e:
        log.exception("Garmin 동기화 실패")
        await update.message.reply_text(f"❌ 동기화 실패: {e}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    acts, well, warn = _activities_or_warn()
    if warn:
        await update.message.reply_text(warn)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    report = coach.status_report(acts, well)
    await update.message.reply_text(report)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    acts, well, warn = _activities_or_warn()
    if warn:
        await update.message.reply_text(warn)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    plan = coach.today_workout(acts, well)
    await update.message.reply_text(plan)


async def cmd_last(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    acts, well, warn = _activities_or_warn()
    if warn:
        await update.message.reply_text(warn)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    analysis = coach.analyze_last_run(acts, well)
    await update.message.reply_text(analysis)


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    acts, well, warn = _activities_or_warn()
    if warn:
        await update.message.reply_text(warn)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    plan = coach.weekly_plan(acts, well)
    await update.message.reply_text(plan)


async def cmd_zones(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    acts = load_activities()
    vdot = an.best_vdot_from_activities(acts) if acts else None
    if not vdot:
        await update.message.reply_text("VDOT를 계산할 데이터가 없어. /sync 먼저 해줘.")
        return
    zones = an.pace_zones(vdot)
    lines = [f"🏃 *페이스존* (VDOT {vdot:.1f})\n"]
    descriptions = {
        "E (Easy)": "유산소 기반 — 대화 가능 여유 페이스",
        "M (Marathon)": "마라톤 목표 페이스",
        "T (Threshold)": "젖산역치 — 불편하지만 유지 가능",
        "I (Interval)": "VO2max — 6분 이내 반복",
        "R (Repetition)": "신경근 속도 — 400m 이하 반복",
    }
    for name, (fast, slow) in zones.items():
        lines.append(
            f"*{name}*\n  {an.format_pace(fast)} ~ {an.format_pace(slow)}\n  _{descriptions.get(name, '')}_\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """사용: /goal 서울마라톤 42K 2026-10-18 3:30:00"""
    args = ctx.args or []
    if len(args) < 3:
        mem = load_memory()
        g = mem.get("goal")
        current = (
            f"현재 목표: {g['race']} {g['distance']} {g['date']} (목표기록: {g.get('target_time','미정')})"
            if g else "설정된 목표 없음."
        )
        await update.message.reply_text(
            f"{current}\n\n"
            "설정하려면:\n`/goal 레이스이름 거리 날짜(YYYY-MM-DD) 목표기록(HH:MM:SS)`\n\n"
            "예: `/goal 서울마라톤 42K 2026-10-18 3:30:00`",
            parse_mode="Markdown",
        )
        return
    race = args[0]
    dist = args[1]
    date = args[2]
    target = args[3] if len(args) >= 4 else ""
    set_goal(race, dist, date, target)
    await update.message.reply_text(
        f"✅ 목표 설정 완료!\n"
        f"🎯 {race} {dist} — {date}"
        + (f" 목표 {target}" if target else "")
    )


async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """사용: /note 오른쪽 무릎 약간 통증"""
    if not ctx.args:
        await update.message.reply_text("예: `/note 오른쪽 무릎 통증`", parse_mode="Markdown")
        return
    note = " ".join(ctx.args)
    add_coaching_note(note)
    await update.message.reply_text(f"📝 코칭 메모 저장: {note}")


# ── 자유 텍스트 → 대화형 코치 ────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    acts, well, warn = _activities_or_warn()
    user_msg = update.message.text or ""
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        reply = coach.chat(user_msg, acts or [], well or [])
        await update.message.reply_text(reply)
    except Exception as e:
        log.exception("코치 응답 오류")
        await update.message.reply_text(f"오류가 발생했어: {e}")


# ── 봇 실행 ───────────────────────────────────────────────────────────────────

def run_bot() -> None:
    import datetime
    import pytz
    from scheduler import check_new_runs, morning_readiness, weekly_report

    token = os.getenv("RUNCOACH_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("RUNCOACH_BOT_TOKEN 환경변수 필요")

    KST = pytz.timezone("Asia/Seoul")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("zones", cmd_zones))
    app.add_handler(CommandHandler("goal", cmd_goal))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    jq = app.job_queue
    # ① 런 후 자동 분석 — 5분마다 Garmin 폴링
    jq.run_repeating(check_new_runs, interval=300, first=30, name="run_detector")
    # ② 아침 레디니스 — 매일 06:30 KST
    jq.run_daily(morning_readiness, time=datetime.time(6, 30, 0, tzinfo=KST), name="morning_readiness")
    # ③ 주간 리포트 — 매주 월요일 09:00 KST
    jq.run_daily(weekly_report, time=datetime.time(9, 0, 0, tzinfo=KST), days=(0,), name="weekly_report")

    async def error_handler(update, context) -> None:
        from telegram.error import Conflict
        import asyncio
        if isinstance(context.error, Conflict):
            log.warning("Telegram Conflict — 60초 대기 후 재시도")
            await asyncio.sleep(60)
        else:
            log.exception("봇 오류: %s", context.error)

    app.add_error_handler(error_handler)
    log.info("러닝 코치 봇 시작 (자동 알림 3종 활성화)")
    app.run_polling(drop_pending_updates=True)
