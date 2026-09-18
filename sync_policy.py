# 동기화 루프 정책 — ★순수 판단만 한다.★ 네트워크도 파일 입출력도 없다.
#
# 무엇을 막으려는 것인가
#   웰니스 수집은 하루에 한 번씩 70회를 도는데, 각 회차가 실패를 그냥 삼킨다. 전부 실패해도
#   ★빈 목록이 그대로 파일을 덮어쓴다.★ 그러면 쌓여 있던 기록이 한 번에 사라지고,
#   실패는 아무 데도 안 남는다. 조용히 사라지는 쪽이라 한참 뒤에야 알아챈다.
#
#   그래서 둘로 나눠 판단한다.
#   · 계속 실패하면 ★일찍 멈춘다★ — 70번을 끝까지 두드리면 차단만 깊어진다
#   · 결과가 이전보다 비었으면 ★쓰지 않는다★ — 있는 기록을 없는 기록으로 덮지 않는다
from __future__ import annotations

from typing import Any, Sequence

# 연속 실패가 이 횟수에 닿으면 루프를 멈춘다. 몇 건 실패는 흔하지만 연속 실패는 다른 신호다.
MAX_CONSECUTIVE_FAILURES = 3


def should_stop(consecutive_failures: int) -> bool:
    """연속 실패가 한계에 닿았나. 닿았으면 남은 회차를 돌지 않는다."""
    return consecutive_failures >= MAX_CONSECUTIVE_FAILURES


def should_write(fetched: Sequence[Any] | None, previous: Sequence[Any] | None) -> bool:
    """이번에 받은 결과로 파일을 덮어써도 되나.

    · 받은 게 비었는데 이전에 있었으면 ★쓰지 않는다★ — 이게 기록이 통째로 사라지는 경로다
    · 이전에도 없었으면 쓴다(빈 파일을 만드는 것은 손실이 아니다)
    · 받은 게 이전보다 ★적어도 쓴다★ — 기간이 줄었을 수 있고, 여기서 막으면 정상 축소가 영영 반영되지 않는다.
      막는 것은 ★'전부 잃는 경우' 하나★ 다
    """
    got = len(fetched or [])
    had = len(previous or [])
    if got == 0 and had > 0:
        return False
    return True


def write_skip_reason(fetched: Sequence[Any] | None, previous: Sequence[Any] | None) -> str | None:
    """쓰지 않기로 했을 때 로그에 남길 이유. 쓸 때는 None.

    ★이유를 남기지 않으면 '조용히 안 씀' 이 되어 원래 문제와 같아진다.★
    """
    if should_write(fetched, previous):
        return None
    return f"수집 결과가 비었고 기존 기록이 {len(previous or [])}건 있어 덮어쓰지 않았다"
