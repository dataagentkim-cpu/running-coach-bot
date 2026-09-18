# 데이터 신선도 표시 — ★한 곳에서만 만든다.★ 파일 입출력도 네트워크도 없다.
#
# 왜 공용으로 두나
#   답변 경로가 여럿이다(/today · /last · /week · 자유대화 · 아침 레디니스). 진입점마다 따로
#   붙이면 ★새 경로가 생길 때 빠뜨린다★ — 실제로 아침 메시지가 이틀 동안 09-06 자 캐시로
#   답하면서 아무 표시가 없었다. 메시지는 매일 왔고 형태도 비슷해서 아무도 몰랐다.
#   ★낡은 데이터로 그럴듯하게 답하는 것이 침묵보다 나쁘다.★
#
# 값은 인자로 받는다. 파일 mtime 을 읽는 것은 부르는 쪽의 일이다.
from __future__ import annotations

from datetime import date, datetime, timedelta

# 이 시간을 넘기면 "낡았다" 고 표시한다. 하루 한 번 동기화가 정상이라 그 두 배를 경계로 둔다.
STALE_AFTER = timedelta(hours=48)


def age_text(now: datetime, synced_at: datetime | None) -> str:
    """동기화 시각 → '3시간 전' 같은 문구. 없으면 그렇게 말한다."""
    if synced_at is None:
        return "동기화 기록 없음"
    delta = now - synced_at
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 0)}분 전"
    hours = mins // 60
    if hours < 24:
        return f"{hours}시간 전"
    return f"{hours // 24}일 전"


def is_stale(now: datetime, synced_at: datetime | None) -> bool:
    """없거나 오래됐으면 낡은 것으로 본다."""
    return synced_at is None or (now - synced_at) > STALE_AFTER


def line(
    now: datetime,
    synced_at: datetime | None,
    wellness_date: str | date | None = None,
    missing: list[str] | None = None,
) -> str | None:
    """답변에 붙일 신선도 한 줄. 붙일 게 없으면 None.

    · 낡았으면 언제 것인지 밝힌다
    · 웰니스처럼 기준 날짜가 따로 있으면 ★그 날짜를 적는다★ — "09-06 기준" 이 핵심이다
    · 이번 답에서 빠진 것이 있으면 이름을 적는다. ★조용히 빼지 않는다★
    """
    parts: list[str] = []
    if is_stale(now, synced_at):
        parts.append(f"데이터가 낡았어 (마지막 동기화 {age_text(now, synced_at)})")
    if wellness_date:
        d = wellness_date.strftime("%m-%d") if isinstance(wellness_date, date) else str(wellness_date)
        if _is_old(now, d):
            parts.append(f"웰니스는 {d} 기준")
    if missing:
        parts.append(f"{'·'.join(missing)} 없이 보냄")
    if not parts:
        return None
    return "ⓘ " + " · ".join(parts)


def _is_old(now: datetime, day_str: str) -> bool:
    """웰니스 기준일이 오늘이 아니면 밝힌다. 못 읽는 형식이면 밝히는 쪽으로 기운다."""
    for fmt in ("%Y-%m-%d", "%m-%d"):
        try:
            d = datetime.strptime(day_str, fmt)
        except ValueError:
            continue
        if fmt == "%m-%d":
            d = d.replace(year=now.year)
        return d.date() != now.date()
    return True
