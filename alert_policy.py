# Garmin 접속 실패 알림 정책 — ★순수 판단만 한다.★
# 네트워크도, 파일 입출력도, 전송도 없다. ★상태 파일 스키마를 알지 못한다.★
#
# 왜 이렇게 떼어놨나
#   · "언제 알릴 것인가" 는 시간 조건이 얽혀 있다(30분 지속 · 하루 1회 · 즉시). 전송 코드와
#     섞이면 시험이 실제로 30분을 기다려야 한다. 판단만 떼면 시각을 인자로 넣어 잰다.
#   · 상태는 ★이미 다른 곳에서 디스크에 저장되고 있고 그 필드 이름을 우리는 모른다.★
#     여기서 읽고 쓰면 이름이 어긋날 때 조용히 안 맞는다. 그래서 값을 ★인자로 받고★
#     새 값을 ★돌려주기만★ 한다. 저장은 부르는 쪽이 자기 스키마로 한다.
#
# 규칙
#   · 인증 실패는 ★즉시★ 알린다. 사람이 손대야 풀리고 그동안 모든 호출이 막힌다
#   · 그 외 실패는 ★30분 지속★ 됐을 때 1회. 잠깐 끊긴 것으로 알리지 않는다
#   · 계속 실패하면 ★하루 1회★ 로 억제한다. 같은 사실을 반복하면 읽지 않게 된다
#   · ★억제는 영구가 아니다★ — 하루가 지나면 다시 알린다. 영구 억제는 그 자체가 조용한 실패다
#   · 복구는 1회 알린다. ★알린 적이 있을 때만★ — 알리지 않은 실패의 복구는 알릴 것이 없다
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

FIRST_ALERT_AFTER = timedelta(minutes=30)
REPEAT_EVERY = timedelta(days=1)

AUTH = "auth"
OTHER = "other"
OK = "ok"


@dataclass(frozen=True)
class Decision:
    """무엇을 보낼지와, 부르는 쪽이 저장할 새 값.

    ★필드를 어디에 어떤 이름으로 저장할지는 부르는 쪽이 정한다.★ 여기서는 값만 준다.
    None 은 "지우라" 는 뜻이다(복구 시 실패 흔적을 비운다).
    """

    alert: str | None
    fail_since: datetime | None
    last_notified_at: datetime | None
    last_success_at: datetime | None


def decide(
    now: datetime,
    last_success_at: datetime | None,
    fail_since: datetime | None,
    last_notified_at: datetime | None,
    error_kind: str,
    backoff_step: int = 0,
) -> Decision:
    """호출 결과 하나를 보고 알림 여부를 정한다.

    error_kind: "ok" 성공 · "auth" 인증 실패 · 그 외 문자열은 일반 실패로 본다.
    """
    if error_kind == OK:
        return _on_success(now, fail_since, last_notified_at)
    return _on_failure(now, last_success_at, fail_since, last_notified_at, error_kind, backoff_step)


def _on_success(now, fail_since, last_notified_at) -> Decision:
    recovered = fail_since is not None and last_notified_at is not None
    alert = None
    if recovered:
        alert = f"✅ Garmin 연결 복구됐어. (중단 {format_duration(now - fail_since)})"
    return Decision(alert=alert, fail_since=None, last_notified_at=None, last_success_at=now)


def _on_failure(now, last_success_at, fail_since, last_notified_at, error_kind, backoff_step) -> Decision:
    started = fail_since or now
    keep = Decision(None, started, last_notified_at, last_success_at)

    if error_kind == AUTH:
        # ★인증 실패는 30분을 기다리지 않는다.★ 다만 하루 1회 억제는 받는다.
        if last_notified_at is not None and now - last_notified_at < REPEAT_EVERY:
            return keep
        return Decision(_auth_text(backoff_step), started, now, last_success_at)

    if last_notified_at is None:
        if now - started < FIRST_ALERT_AFTER:
            return keep
        return Decision(_first_text(now - started, backoff_step), started, now, last_success_at)

    if now - last_notified_at < REPEAT_EVERY:
        return keep
    # 하루가 지났다 — 다시 알린다. 억제가 영구가 되면 그게 다시 조용한 실패다.
    return Decision(_repeat_text(now - started, backoff_step), started, now, last_success_at)


def status_line(
    now: datetime,
    last_success_at: datetime | None,
    fail_since: datetime | None = None,
    backoff_step: int = 0,
) -> str:
    """`/status` 에 붙일 한 줄. 마지막 성공 시각과 백오프 단계를 보여준다."""
    if last_success_at is None:
        last = "기록 없음"
    else:
        last = f"{last_success_at:%m/%d %H:%M} ({format_duration(now - last_success_at)} 전)"
    line = f"Garmin 마지막 성공: {last}"
    if fail_since is not None:
        line += f" · 실패 지속 {format_duration(now - fail_since)}"
    if backoff_step:
        line += f" · 백오프 {backoff_step}단계"
    return line


def _auth_text(step: int) -> str:
    return (
        "🔐 Garmin 인증이 실패했어. 토큰이나 비밀번호를 확인해줘 — 그때까지 동기화가 멈춰."
        + _step_suffix(step)
    )


def _first_text(down: timedelta, step: int) -> str:
    return f"⚠️ Garmin 연결이 {format_duration(down)}째 안 돼. 자동으로 다시 시도 중이야." + _step_suffix(step)


def _repeat_text(down: timedelta, step: int) -> str:
    return f"⚠️ Garmin 연결이 아직 안 돼 ({format_duration(down)}째)." + _step_suffix(step)


def _step_suffix(step: int) -> str:
    return f" (백오프 {step}단계)" if step else ""


def format_duration(d: timedelta) -> str:
    mins = int(d.total_seconds() // 60)
    if mins < 60:
        return f"{mins}분"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours}시간" + (f" {mins}분" if mins else "")
    days, hours = divmod(hours, 24)
    return f"{days}일" + (f" {hours}시간" if hours else "")
