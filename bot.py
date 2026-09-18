# 텔레그램 봇 — 대화형 러닝 코칭 인터페이스 (python-telegram-bot v20+)
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import analytics as an
import workout_builder as wb
from coach import (
    RunningCoach,
    add_coaching_note,
    clear_pending_spec,
    load_memory,
    pending_spec,
    save_memory,
    set_goal,
)
from garmin_client import (
    GarminClient,
    last_sync_time,
    load_activities,
    load_wellness,
    record_workout,
)

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
        reply, spec = coach.chat(user_msg, acts or [], well or [])
        # 코치가 세션을 제안했으면 답 밑에 미리보기와 버튼을 붙인다.
        # ★자유대화만으로 업로드까지 가지 않게 한다★ — 계정에 쌓인 것은 되돌리는 비용이 있다.
        if spec:
            await update.message.reply_text(reply)
            await update.message.reply_text(_preview(spec), reply_markup=_upload_keyboard())
        else:
            await update.message.reply_text(reply)
    except Exception as e:
        log.exception("코치 응답 오류")
        await update.message.reply_text(f"오류가 발생했어: {e}")


# ── 워크아웃 밀어넣기 ────────────────────────────────────────────────────────
#
# 업로드는 ★항상 확인 한 단계★ 를 거친다. 명령이든 자유대화든 미리보기를 먼저 보여주고
# 버튼을 눌러야 나간다.

UPLOAD_CALLBACK = "wk:upload"
CANCEL_CALLBACK = "wk:cancel"


def _upload_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⌚ 시계로 보내기", callback_data=UPLOAD_CALLBACK),
        InlineKeyboardButton("취소", callback_data=CANCEL_CALLBACK),
    ]])


def _describe_step(step: dict, indent: str = "") -> list[str]:
    cond = step.get("condition") or {}
    if "distance_m" in cond:
        amount = f"{int(cond['distance_m'])}m"
    elif "duration_sec" in cond:
        amount = f"{int(cond['duration_sec']) // 60}분"
    else:
        amount = "?"
    target = step.get("target") or {}
    if "hr_zone" in target:
        tgt = f"Z{target['hr_zone']}"
    elif "pace_zone" in target:
        tgt = f"{target['pace_zone']}존"
    elif "pace_range_sec" in target:
        lo, hi = target["pace_range_sec"]
        tgt = f"{an.format_pace(lo)}~{an.format_pace(hi)}"
    else:
        tgt = "목표 자동"
    return [f"{indent}· {step.get('type')} {amount} ({tgt})"]


def _preview(spec: dict) -> str:
    """업로드 전에 보여줄 미리보기. 사용자가 여기서 틀린 걸 잡아야 한다."""
    lines = [f"⌚ {spec.get('name') or wb.name_for(spec, spec.get('date'))}"]
    if spec.get("date"):
        lines.append(f"예정일: {spec['date']}")
    if spec.get("description"):
        lines.append(spec["description"])
    lines.append("")
    for step in spec.get("steps") or []:
        if step.get("type") == "repeat":
            lines.append(f"· {step.get('iterations')}회 반복")
            for child in step.get("steps") or []:
                lines.extend(_describe_step(child, "   "))
        else:
            lines.extend(_describe_step(step))
    return "\n".join(lines)


def _pace_zones_now() -> dict | None:
    acts = load_activities()
    if not acts:
        return None
    return an.pace_zones(an.best_vdot_from_activities(acts))


async def cmd_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/interval 400m x8 @R 200m 회복` — LLM 을 타지 않는다. 파싱만 한다."""
    text = " ".join(ctx.args or [])
    spec = wb.parse_interval_text(text, _today_kst())
    if not spec:
        await update.message.reply_text(
            "못 읽었어. 예: /interval 400m x8 @R 200m 회복\n"
            "그냥 말로 설명해도 돼 — 코치가 세션을 제안해줄게."
        )
        return
    mem = load_memory()
    mem["pending_spec"] = spec
    save_memory(mem)
    await update.message.reply_text(_preview(spec), reply_markup=_upload_keyboard())


async def cmd_workout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """직전에 제안된 세션을 다시 꺼낸다."""
    spec = pending_spec()
    if not spec:
        await update.message.reply_text("대기 중인 세션이 없어. /interval 로 직접 지정하거나 오늘 훈련을 물어봐.")
        return
    await update.message.reply_text(_preview(spec), reply_markup=_upload_keyboard())


async def on_workout_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """확인 버튼. ★여기서만 Garmin 에 쓴다.★ 호출은 업로드 1 + 예약 1 = 2회."""
    query = update.callback_query
    await query.answer()
    if query.data == CANCEL_CALLBACK:
        clear_pending_spec()
        await query.edit_message_text("취소했어.")
        return

    spec = pending_spec()
    if not spec:
        await query.edit_message_text("대기 중인 세션이 없어. 다시 만들어줘.")
        return

    try:
        workout = wb.build(spec, _pace_zones_now())
    except wb.SpecError as e:
        await query.edit_message_text(f"세션을 만들 수 없어: {e}")
        return

    try:
        result = client_for_upload().upload_workout(workout)
        workout_id = result.get("workoutId") if isinstance(result, dict) else None
        if workout_id is None:
            raise RuntimeError(f"업로드 응답에 workoutId 가 없다: {result}")
        scheduled = False
        date_str = spec.get("date")
        if date_str:
            client_for_upload().schedule_workout(workout_id, date_str)
            scheduled = True
        record_workout({
            "workout_id": workout_id,
            "name": workout.workoutName,
            "date": date_str,
            "spec": spec,
            "scheduled": scheduled,
        })
        clear_pending_spec()
    except Exception as e:
        log.exception("워크아웃 업로드 실패")
        # ★기록을 남기지 않는다★ — 실패한 업로드를 기록하면 정리가 없는 것을 지우려 든다.
        await query.edit_message_text(f"업로드 실패: {e}\n다시 시도하려면 /workout")
        return

    when = f"{date_str} 로 예약했어" if scheduled else "라이브러리에 올렸어"
    await query.edit_message_text(
        f"⌚ {workout.workoutName} {when}.\nGarmin Connect 앱이 시계와 동기화하면 보인다."
    )


_upload_client: GarminClient | None = None


def client_for_upload() -> GarminClient:
    """업로드용 클라이언트를 재사용한다 — 매번 새로 만들면 재로그인이 쌓인다."""
    global _upload_client
    if _upload_client is None:
        _upload_client = GarminClient()
    return _upload_client


def _today_kst() -> str:
    import datetime as _dt

    import pytz as _pytz
    return _dt.datetime.now(_pytz.timezone("Asia/Seoul")).strftime("%Y-%m-%d")


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
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("workout", cmd_workout))
    app.add_handler(CallbackQueryHandler(on_workout_button, pattern=r"^wk:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    jq = app.job_queue
    # ① 런 후 자동 분석 — 5분마다 Garmin 폴링
    jq.run_repeating(check_new_runs, interval=300, first=600, name="run_detector")  # ★first=30 이면 재기동 30초 만에 첫 발사★ — 장애 중 재기동이 곧 재폭주였다
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
