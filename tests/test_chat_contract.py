# chat() 의 반환 계약 회귀 시험 — ★네트워크·LLM 없음★ (chat 을 스텁으로 바꾼다).
#
# 왜 있나: chat() 이 (응답, 스펙) 튜플을 돌려주도록 바뀌었는데 래퍼 3개와 scheduler 가
# 그대로 넘기고 있었다. 사용자에게 튜플이 그대로 나가거나, 문자열 이어붙이기에서
# TypeError 가 try/except 안에 삼켜져 ★기능이 조용히 사라진다.★
# ★튜플이 필요한 곳은 자유대화 핸들러 하나뿐★ 이라는 사실을 여기서 고정한다.
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import coach as c  # noqa: E402

SENTINEL_SPEC = {"steps": [{"type": "interval", "condition": {"distance_m": 400}}]}


class _StubCoach(c.RunningCoach):
    """chat 만 바꿔치기한다 — 래퍼가 반환값을 어떻게 다루는지만 본다."""

    def __init__(self):
        self.calls = 0

    def chat(self, user_message, activities, wellness):
        self.calls += 1
        return "코치 응답", SENTINEL_SPEC


ACTS = [{"activityId": 1, "startTimeLocal": "2026-09-18 07:00:00", "distance": 10000,
         "duration": 3000, "averageHR": 150, "activityType": {"typeKey": "running"}}]


@pytest.mark.parametrize("name", ["today_workout", "analyze_last_run", "weekly_plan"])
def test_래퍼는_문자열만_돌려준다(name):
    stub = _StubCoach()
    out = getattr(stub, name)(ACTS, [])
    assert isinstance(out, str), f"{name} 이 {type(out).__name__} 을 돌려준다"
    assert out == "코치 응답"


def test_스텁이_실제로_불렸다():
    # 래퍼가 chat 을 안 부르고 조기 반환하면 위 단정이 공허하게 통과한다.
    stub = _StubCoach()
    stub.today_workout(ACTS, [])
    assert stub.calls == 1


def test_analyze_last_run_은_데이터없으면_안내문을_준다():
    # 이 경로는 chat 을 타지 않는다 — 그래도 문자열이어야 한다.
    out = _StubCoach().analyze_last_run([], [])
    assert isinstance(out, str)


# ── 호출부 전수 ──────────────────────────────────────────────────────────────

_CALL = re.compile(r"^(?!\s*#).*\.chat\(", re.M)


def _chat_call_lines() -> list[str]:
    hits = []
    for f in sorted(ROOT.glob("*.py")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if ".chat(" in line and not line.lstrip().startswith("#"):
                hits.append(f"{f.name}:{i}:{line.strip()}")
    return hits


def test_모든_chat_호출부가_튜플을_다룬다():
    """★'한 곳뿐' 이라고 믿지 않고 전수로 센다.★

    허용 형태는 둘뿐이다 — 언패킹(`a, b = ...`) 또는 응답만 꺼내기(`...[0]`).
    새 호출부가 생기면 여기서 걸린다.
    """
    bad = []
    for hit in _chat_call_lines():
        line = hit.split(":", 2)[2]
        if line.startswith("def ") or "self.calls" in line:
            continue
        unpacks = re.search(r"\w+\s*,\s*\w+\s*=\s*.*\.chat\(", line)
        if not unpacks and "[0]" not in _tail_of(hit):
            bad.append(hit)
    assert not bad, f"튜플을 그대로 넘기는 호출부: {bad}"


def _tail_of(hit: str) -> str:
    """여러 줄 호출이라 닫는 괄호까지 읽어 `[0]` 이 붙었는지 본다."""
    fname, lineno, _ = hit.split(":", 2)
    lines = (ROOT / fname).read_text(encoding="utf-8").splitlines()
    start = int(lineno) - 1
    depth = 0
    for i in range(start, min(start + 20, len(lines))):
        depth += lines[i].count("(") - lines[i].count(")")
        if depth <= 0 and i > start:
            return lines[i]
        if depth <= 0 and i == start:
            return lines[i]
    return ""


def test_전수_검사가_실제로_호출부를_찾는다():
    # 정규식이 아무것도 못 찾으면 위 시험이 공허하게 통과한다.
    assert len(_chat_call_lines()) >= 4
