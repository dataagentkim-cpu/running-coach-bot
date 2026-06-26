# Claude API 기반 러닝 코치 — 피트니스 컨텍스트 + 영구 메모리
from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import analytics as an

load_dotenv()

MEMORY_FILE = Path(__file__).parent / "data" / "coach_memory.json"
HISTORY_LIMIT = 20  # 대화 히스토리 최대 턴 수


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

def build_fitness_context(activities: list[dict], wellness: list[dict]) -> str:
    """현재 피트니스 상태를 코치 프롬프트용 텍스트로 변환."""
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

    # 최근 4주 요약
    weekly = an.weekly_summary(activities, weeks=4)
    if weekly:
        lines.append("\n## 최근 주간 훈련량")
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
"""


# ── 대화 인터페이스 ──────────────────────────────────────────────────────────

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
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            messages=history[-HISTORY_LIMIT:],
        )
        reply = response.content[0].text

        history.append({"role": "assistant", "content": reply})
        mem["conversation"] = history[-HISTORY_LIMIT:]
        save_memory(mem)
        return reply

    def today_workout(self, activities: list[dict], wellness: list[dict]) -> str:
        """오늘 훈련 처방."""
        from datetime import datetime
        weekday = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]
        return self.chat(
            f"오늘({weekday}요일) 훈련 처방을 내려줘. 현재 피트니스 상태와 컨디션을 보고 구체적인 워크아웃을 제시해줘.",
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
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": "내 현재 피트니스 상태를 분석해서 한눈에 볼 수 있는 요약 리포트를 만들어줘. VDOT, 체력/피로/컨디션 수치 해석 포함."}],
        )
        return response.content[0].text
