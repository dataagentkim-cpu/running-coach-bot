# Claude API 기반 러닝 코치 — 피트니스 컨텍스트 + 영구 메모리
from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import analytics as an
from garmin_client import load_workouts
from workout_builder import WORKOUT_SPEC_TOOL

load_dotenv()

MEMORY_FILE = Path(__file__).parent / "data" / "coach_memory.json"
HISTORY_LIMIT = 20  # 대화 히스토리 최대 턴 수


# 모델을 한 곳에서만 정의한다. 이전엔 두 호출부에 하드코딩돼 있어 투자봇이 sonnet-5로
# 넘어갈 때 이쪽만 sonnet-4-6에 남아 있었다.
MODEL = "claude-opus-5"


def _default_memory() -> dict:
    return {
        "goal": None,          # {"race": str, "distance": str, "date": str, "target_time": str}
        "injury_log": [],      # [{"date": str, "note": str}]
        "coaching_notes": [],  # [{"date": str, "note": str}]
        "conversation": [],    # [{"role": "user"|"assistant", "content": str}]
    }


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text())
        except Exception:
            pass
    return _default_memory()


def save_memory(mem: dict) -> None:
    MEMORY_FILE.parent.mkdir(exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=2, default=str))


def set_goal(race: str, distance: str, date: str, target_time: str = "") -> None:
    mem = load_memory()
    mem["goal"] = {"race": race, "distance": distance, "date": date, "target_time": target_time}
    save_memory(mem)


def add_coaching_note(note: str) -> None:
    from datetime import datetime
    mem = load_memory()
    mem["coaching_notes"].append({"date": datetime.now().strftime("%Y-%m-%d"), "note": note})
    mem["coaching_notes"] = mem["coaching_notes"][-20:]  # 최근 20개만 유지
    save_memory(mem)


# ── 컨텍스트 빌더 ────────────────────────────────────────────────────────────

def _zone_summary(zones: list[dict]) -> str:
    """심박존 체류 시간 → 'Z3 7.8분 · Z4 33.9분 → 고강도 82%' 형태.

    평균 페이스가 뭉개는 인터벌 자극을 드러내려는 목적이다.
    """
    total = sum(z.get("secsInZone") or 0 for z in zones)
    if total <= 0:
        return ""

    parts = [
        f"Z{z.get('zoneNumber')} {(z.get('secsInZone') or 0) / 60:.1f}분"
        for z in zones
        if (z.get("secsInZone") or 0) >= 30  # 30초 미만은 노이즈
    ]
    if not parts:
        return ""

    hard = sum(
        z.get("secsInZone") or 0 for z in zones if (z.get("zoneNumber") or 0) >= 4
    )
    return " · ".join(parts) + f" → 고강도 {hard / total * 100:.0f}%"


def _plan_vs_actual(spec: dict, zones: list[dict]) -> str:
    """계획한 고강도 시간 ↔ 실제 체류 시간 한 줄. 대조할 게 없으면 빈 문자열.

    이 줄이 다음 처방의 입력이 된다 — 설계·실행·측정이 한 바퀴 도는 자리다.
    새 데이터 수집도, 새 API 호출도 필요 없다. 심박존은 이미 런 감지가 받아온다.
    """
    if not spec or not zones:
        return ""
    planned_sec = _planned_hard_seconds(spec)
    if planned_sec <= 0:
        return ""
    actual_sec = sum(
        z.get("secsInZone") or 0 for z in zones if (z.get("zoneNumber") or 0) >= 4
    )
    if actual_sec <= 0:
        return f"계획 고강도 {planned_sec / 60:.0f}분 / 실제 Z4+ 기록 없음"
    diff = (actual_sec - planned_sec) / planned_sec * 100
    return (
        f"계획 고강도 {planned_sec / 60:.0f}분 / 실제 Z4+ {actual_sec / 60:.1f}분 ({diff:+.0f}%)"
    )


def _planned_hard_seconds(spec: dict) -> float:
    """스펙에서 '고강도로 의도한' 시간을 센다 — interval 스텝만, 반복 횟수를 곱한다.

    거리로 지정된 구간은 목표 페이스로 환산한다. 목표가 없으면 셀 수 없으니 뺀다 —
    모르는 것을 추정해 넣으면 대조 자체가 거짓이 된다.
    """
    total = 0.0
    for step in spec.get("steps") or []:
        if step.get("type") == "repeat":
            total += int(step.get("iterations", 1)) * _planned_hard_seconds(step)
            continue
        if step.get("type") != "interval":
            continue
        cond = step.get("condition") or {}
        if "duration_sec" in cond:
            total += float(cond["duration_sec"])
            continue
        dist = float(cond.get("distance_m") or 0)
        pace = (step.get("target") or {}).get("pace_range_sec")
        if dist > 0 and pace:
            total += dist / 1000.0 * (float(pace[0]) + float(pace[1])) / 2
    return total


def spec_for_date(date_str: str) -> dict | None:
    """그 날짜에 우리가 올린 워크아웃의 스펙. 없으면 None.

    ★하루 1세션 가정이다.★ 예약 워크아웃과 완료 활동을 Garmin 이 연결해 주는지 확인하지
    못해서 날짜(KST)로 맞춘다. 하루 2세션이면 어긋난다.
    """
    for item in reversed(load_workouts()):
        if item.get("date") == date_str:
            return item.get("spec")
    return None


def build_fitness_context(activities: list[dict], wellness: list[dict]) -> str:
    """현재 피트니스 상태를 코치 프롬프트용 텍스트로 변환."""
    import pytz
    from datetime import datetime, timedelta
    from garmin_client import load_hr_zones
    KST = pytz.timezone("Asia/Seoul")
    today_kst = datetime.now(KST).date()
    hr_zones = load_hr_zones()

    vdot = an.best_vdot_from_activities(activities)
    load_series = an.daily_load_series(activities, days=90)
    ctl, atl, tsb = an.ctl_atl_tsb(load_series)

    lines = ["## 현재 피트니스 상태"]
    if vdot:
        lines.append(f"- VDOT(VO2max): {vdot:.1f}")
        zones = an.pace_zones(vdot)
        lines.append("- 페이스존:")
        for name, (fast, slow) in zones.items():
            lines.append(f"  • {name}: {an.format_pace(fast)} ~ {an.format_pace(slow)}")

    lines += [
        f"- 체력(CTL, 42일 평균): {ctl}",
        f"- 피로(ATL, 7일 평균):  {atl}",
        f"- 컨디션(TSB = CTL-ATL): {tsb:+.1f} ({'좋음 ✓' if tsb > -10 else '피로 누적 ⚠️' if tsb < -20 else '보통'})",
    ]

    # 이번 주 / 지난 주 실제 일별 러닝 내역 (날짜 혼동 방지)
    week_start = today_kst - timedelta(days=today_kst.weekday())  # 이번 주 월요일
    prev_week_start = week_start - timedelta(days=7)
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"]

    def _acts_in_range(start, end):
        result = []
        for act in activities:
            ts = act.get("beginTimestamp") or act.get("startTimeGMT")
            date_str = an._ts_to_kst_date(ts)
            if not date_str:
                continue
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
            except Exception:
                continue
            if start <= d <= end:
                dist_km = (act.get("distance") or 0) / 1000
                dur_sec = act.get("duration") or act.get("movingDuration") or 0
                hr = act.get("averageHR") or 0
                pace_sec = dur_sec / dist_km if dist_km else 0
                wd = weekday_kr[d.weekday()]
                result.append(
                    f"  {d.strftime('%m/%d')}({wd}): {dist_km:.1f}km @ {an.format_pace(pace_sec)}, 심박 {round(hr)}bpm"
                )
                act_zones = hr_zones.get(str(act.get("activityId")), [])
                zsum = _zone_summary(act_zones)
                if zsum:
                    result.append(f"    └ {zsum}")
                pva = _plan_vs_actual(spec_for_date(d.strftime("%Y-%m-%d")) or {}, act_zones)
                if pva:
                    result.append(f"    └ {pva}")
        return result

    this_week_lines = _acts_in_range(week_start, today_kst)
    prev_week_lines = _acts_in_range(prev_week_start, week_start - timedelta(days=1))

    lines.append(f"\n## 이번 주 실제 러닝 ({week_start.strftime('%m/%d')}~, 오늘: {today_kst.strftime('%m/%d')} {weekday_kr[today_kst.weekday()]}요일)")
    if this_week_lines:
        lines.extend(this_week_lines)
    else:
        lines.append("  아직 기록 없음")

    if prev_week_lines:
        lines.append(f"\n## 지난 주 러닝 ({prev_week_start.strftime('%m/%d')}~{(week_start - timedelta(days=1)).strftime('%m/%d')})")
        lines.extend(prev_week_lines)

    # 최근 4주 요약
    weekly = an.weekly_summary(activities, weeks=4)
    if weekly:
        lines.append("\n## 최근 주간 훈련량 요약")
        for w in weekly:
            lines.append(
                f"- {w['week']}: {w['runs']}회, {w['total_km']}km, "
                f"{w['total_min']}분, 평균심박 {w['avg_hr']}bpm"
            )

    # 마지막 러닝
    last = an.last_run_summary(activities)
    if last:
        lines.append(f"\n## 마지막 러닝 ({last['date']})")
        lines.append(
            f"- {last['name']}: {last['distance_km']}km @ {last['avg_pace']}, "
            f"심박 {last['avg_hr']}bpm"
        )
        if last.get("aerobic_te"):
            lines.append(f"- 유산소 훈련 효과(TE): {last['aerobic_te']}")

    # 웰니스 (최근 3일)
    if wellness:
        recent_wellness = wellness[:3]
        lines.append("\n## 최근 웰니스")
        for w in recent_wellness:
            bb = w.get("bodyBatteryMostRecentValue") or w.get("bodyBatteryChargedValue")
            sleep_sec = w.get("sleepingSeconds") or w.get("sleepSeconds") or 0
            stress = w.get("averageStressLevel")
            parts = [f"  {w['date']}:"]
            if bb:
                parts.append(f"바디배터리 {bb}")
            if sleep_sec:
                parts.append(f"수면 {sleep_sec//3600:.1f}h")
            if stress:
                parts.append(f"스트레스 {stress}")
            if len(parts) > 1:
                lines.append(" ".join(parts))

    return "\n".join(lines)


def _system_prompt(fitness_ctx: str, mem: dict) -> str:
    import pytz
    from datetime import datetime
    KST = pytz.timezone("Asia/Seoul")
    now_kst = datetime.now(KST)
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]
    today_str = now_kst.strftime(f"%Y년 %m월 %d일 ({weekday_kr}요일)")

    goal_str = "없음"
    if mem.get("goal"):
        g = mem["goal"]
        goal_str = f"{g.get('race', '')} {g.get('distance', '')} / 목표일 {g.get('date', '')} / 목표기록 {g.get('target_time', '미정')}"

    notes_str = ""
    if mem.get("coaching_notes"):
        notes_str = "\n".join(
            f"- [{n['date']}] {n['note']}" for n in mem["coaching_notes"][-5:]
        )

    return f"""당신은 Jack Daniels VDOT 시스템과 80/20 훈련법에 정통한 전문 러닝 코치입니다.

## 현재 날짜/시간 (KST)
{today_str}

{fitness_ctx}

## 러너 목표
{goal_str}

## 코칭 노트 (최근 기록)
{notes_str or '없음'}

## 코칭 원칙
1. 실제 데이터(VDOT, CTL/ATL/TSB)에 근거한 구체적 조언을 해라.
2. 처방 시 반드시 구체적 페이스/심박/시간을 명시해라.
3. "왜"를 항상 설명해라 (스포츠 과학적 근거).
4. 피로(TSB < -20)가 쌓였으면 회복을 우선 처방해라.
5. 한국어로 친근하고 명확하게 대화해라.
6. 처방 워크아웃은 목적(E/M/T/I/R)과 함께 제시해라.
7. 러닝 아래 `└ Z3 ... → 고강도 xx%` 라인이 있으면, 그게 그 세션의 실제 자극 크기다.
   인터벌은 평균 페이스가 강도를 뭉개므로, 존 체류 시간을 평균 페이스보다 우선해서 판단해라.
8. 러닝 아래 `└ 계획 고강도 .. / 실제 Z4+ ..` 라인이 있으면, 그게 지난 처방이 실제로 어떻게
   수행됐는지다. 다음 처방의 강도 보정에 반영해라 — 실제가 계획보다 크게 넘쳤으면 줄이고,
   크게 못 미쳤으면 구간 길이나 목표를 다시 봐라.
9. 세션을 시계로 보낼 만한 형태로 제안할 때는 propose_workout 도구를 함께 써라. 설명은 평소대로
   하고, 도구에는 그 설명과 같은 내용을 구조로 담아라. 도구를 썼다고 바로 업로드되지 않는다 —
   사용자가 버튼으로 확인한다.

## 날짜 규칙 (절대 준수)
- "이번 주 실제 러닝" 섹션에 있는 날짜만 완료된 훈련으로 표시해라.
- 오늘({today_str}) 이후 날짜는 계획/처방으로만 표시하고, 절대 완료(✅)로 표시하지 마라.
- 지난 주 기록을 이번 주 날짜에 옮겨 쓰지 마라.
"""


# ── 대화 인터페이스 ──────────────────────────────────────────────────────────

def _reply_text(response) -> str:
    """adaptive thinking이 켜지면 content[0]가 thinking 블록이라 .text가 비어 있다.
    실제 답변은 첫 text 블록에서 꺼내야 한다."""
    for block in response.content:
        if block.type == "text":
            return block.text
    raise RuntimeError("응답에 text 블록이 없음 (max_tokens 부족으로 잘렸을 가능성)")


def _spec_from(response) -> dict | None:
    """응답에 tool_use 블록이 있으면 그 입력(=스펙)을 꺼낸다. 없으면 None."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == WORKOUT_SPEC_TOOL["name"]:
            return dict(block.input)
    return None


def pending_spec() -> dict | None:
    """직전에 제안된 스펙. /workout 이 다시 꺼내 쓴다."""
    return load_memory().get("pending_spec")


def clear_pending_spec() -> None:
    mem = load_memory()
    mem["pending_spec"] = None
    save_memory(mem)


class RunningCoach:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def chat(
        self,
        user_message: str,
        activities: list[dict],
        wellness: list[dict],
    ) -> str:
        """사용자 메시지 → 코치 응답. 대화 히스토리 자동 관리."""
        mem = load_memory()
        fitness_ctx = build_fitness_context(activities, wellness)
        system = _system_prompt(fitness_ctx, mem)

        history = mem.get("conversation", [])
        history.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,  # thinking + 답변 합계 상한. 1024면 사고가 예산을 먹고 답변이 잘린다.
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            # 같은 호출에서 설명과 스펙이 함께 나온다 — 스펙을 얻자고 LLM 을 한 번 더 부르지 않는다.
            tools=[WORKOUT_SPEC_TOOL],
            messages=history[-HISTORY_LIMIT:],
        )
        reply = _reply_text(response)
        spec = _spec_from(response)

        history.append({"role": "assistant", "content": reply})
        mem["conversation"] = history[-HISTORY_LIMIT:]
        # 제안된 스펙은 확인 단계까지 살아 있어야 한다 — 봇이 재시작해도 버튼이 동작하게 저장한다.
        mem["pending_spec"] = spec
        save_memory(mem)
        return reply, spec

    def today_workout(self, activities: list[dict], wellness: list[dict]) -> str:
        """오늘 훈련 처방."""
        import pytz
        from datetime import datetime
        KST = pytz.timezone("Asia/Seoul")
        now_kst = datetime.now(KST)
        weekday = ["월", "화", "수", "목", "금", "토", "일"][now_kst.weekday()]
        date_str = now_kst.strftime(f"%m월 %d일 {weekday}요일")
        return self.chat(
            f"오늘({date_str}) 훈련 처방을 내려줘. 현재 피트니스 상태와 컨디션을 보고 구체적인 워크아웃을 제시해줘.",
            activities,
            wellness,
        )

    def analyze_last_run(self, activities: list[dict], wellness: list[dict]) -> str:
        """마지막 러닝 분석."""
        last = an.last_run_summary(activities)
        if not last:
            return "아직 동기화된 활동이 없어. /sync로 Garmin 데이터를 먼저 불러와줘."
        return self.chat(
            f"내 마지막 러닝({last['date']}, {last['distance_km']}km @ {last['avg_pace']}, "
            f"심박 {last['avg_hr']}bpm)을 분석해줘. 잘한 점, 개선점, 다음 훈련에 반영할 점을 알려줘.",
            activities,
            wellness,
        )

    def weekly_plan(self, activities: list[dict], wellness: list[dict]) -> str:
        """이번 주 훈련 계획."""
        return self.chat(
            "이번 주 훈련 계획을 요일별로 짜줘. 목적(E/T/인터벌/롱런/휴식)과 구체적 워크아웃을 포함해줘.",
            activities,
            wellness,
        )

    def status_report(self, activities: list[dict], wellness: list[dict]) -> str:
        """현재 피트니스 상태 리포트."""
        fitness_ctx = build_fitness_context(activities, wellness)
        mem = load_memory()
        system = _system_prompt(fitness_ctx, mem)

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=2048,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=system,
            messages=[{"role": "user", "content": "내 현재 피트니스 상태를 분석해서 한눈에 볼 수 있는 요약 리포트를 만들어줘. VDOT, 체력/피로/컨디션 수치 해석 포함."}],
        )
        return _reply_text(response)
