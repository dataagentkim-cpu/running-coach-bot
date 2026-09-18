# Garmin Connect 인증 + 러닝 활동/웰니스 데이터 수집 클라이언트
from __future__ import annotations

import json
import logging
import os
import re
import time

import sync_policy
import pytz
from datetime import date, datetime, timedelta
KST = pytz.timezone("Asia/Seoul")
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

GARTH_HOME = Path.home() / ".garth_runcoach"
DATA_DIR = Path(__file__).parent / "data"

# ── Garmin 접근 게이트 ────────────────────────────────────────────────
# 왜: 2026-09-16~18 장애. 토큰 로그인 실패를 전부 "토큰 만료"로 읽고 곧바로
#   비밀번호 재로그인을 했다. 429 일 때도 같은 경로를 타서 429 → 재로그인 →
#   429 의 자기강화 고리가 됐다(오늘만 재로그인 103회 · 429 287줄).
#   ★429 를 메시지로 판별하지 않는다★ — garth 가 "Failed to retrieve social
#   profile" 로 감싸 던지므로 문자열 판별은 깨진다. 대신 ★재로그인 자체에
#   쿨다운★ 을 건다. 원인이 무엇이든 재로그인 폭주가 불가능해진다.
# 상태를 디스크에 두는 이유: 이전 백오프는 모듈 전역이라 ★재기동하면 사라졌다★.
_STATE_PATH = DATA_DIR / "garmin_state.json"
_DISABLED_PATH = DATA_DIR / "garmin_disabled"
_BACKOFF_LADDER = [300, 900, 1800, 3600, 7200, 14400, 21600]  # 5m~6h


class GarminBlocked(RuntimeError):
    """지금은 Garmin 을 호출하면 안 된다. 호출부는 조용히 건너뛴다."""


# 알림 정책이 쓰는 두 값. ★게이트의 기존 세 키와 같은 파일에 얹는다.★
# 이미 EC2 에 그 파일이 있고 거기엔 이 키가 없다 — 그래서 읽을 때 기본값을 채워 넣는다.
# 없는 키를 그대로 두면 첫 호출에서 KeyError 가 나고, 그건 알림이 통째로 안 도는 실패다.
_GATE_DEFAULTS = {
    "blocked_until": 0.0,
    "fail_count": 0,
    "last_relogin": 0.0,
    "last_success_at": None,   # ISO 문자열 또는 None
    "last_notified_at": None,
    "fail_since": None,
}


def _load_gate() -> dict:
    try:
        st = json.loads(_STATE_PATH.read_text())
        if not isinstance(st, dict):
            raise ValueError("gate state 가 dict 가 아니다")
    except Exception:
        st = {}
    return {**_GATE_DEFAULTS, **st}


def _save_gate(st: dict) -> None:
    try:
        DATA_DIR.mkdir(exist_ok=True)
        _STATE_PATH.write_text(json.dumps(st))
    except Exception:
        pass


def _block(reason: str) -> None:
    st = _load_gate()
    n = min(int(st.get("fail_count", 0)), len(_BACKOFF_LADDER) - 1)
    wait = _BACKOFF_LADDER[n]
    st["blocked_until"] = time.time() + wait
    st["fail_count"] = int(st.get("fail_count", 0)) + 1
    _save_gate(st)
    log.warning("Garmin 차단 %d분 (%s)", wait // 60, reason)


def garmin_gate_status() -> dict:
    """/status 등에서 읽는다. 호출 없음."""
    st = _load_gate()
    st["disabled_file"] = _DISABLED_PATH.exists()
    st["blocked_sec_left"] = max(0, int(st.get("blocked_until", 0) - time.time()))
    return st


def clear_garmin_gate() -> None:
    """사람이 직접 푼다. ★알림 이력은 남긴다★ — 마지막 성공 시각은 게이트와 무관한 사실이다."""
    st = _load_gate()
    _save_gate({**st, "blocked_until": 0.0, "fail_count": 0, "last_relogin": 0.0})
    log.info("Garmin 게이트 해제됨")

def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None  # 못 읽는 값은 '없음' 으로 본다 — 여기서 죽으면 알림이 통째로 안 돈다


def record_garmin_result(kind: str, now: datetime | None = None) -> str | None:
    """Garmin 호출 결과를 기록하고 ★보낼 알림 문구★ 를 돌려준다. 없으면 None.

    판단은 alert_policy 가 한다(순수). 여기서는 상태 파일에서 값을 꺼내 넘기고 새 값을 적기만 한다.
    kind: alert_policy.OK / AUTH / OTHER.
    """
    import alert_policy as ap

    now = now or datetime.now(KST).replace(tzinfo=None)
    st = _load_gate()
    d = ap.decide(
        now,
        _dt(st.get("last_success_at")),
        _dt(st.get("fail_since")),
        _dt(st.get("last_notified_at")),
        kind,
        int(st.get("fail_count") or 0),
    )
    _save_gate({
        **st,
        "last_success_at": d.last_success_at.isoformat() if d.last_success_at else None,
        "last_notified_at": d.last_notified_at.isoformat() if d.last_notified_at else None,
        "fail_since": d.fail_since.isoformat() if d.fail_since else None,
    })
    return d.alert


def garmin_status_line(now: datetime | None = None) -> str:
    """/status 에 붙일 한 줄. 호출 없음 — 디스크 상태만 읽는다."""
    import alert_policy as ap

    now = now or datetime.now(KST).replace(tzinfo=None)
    st = garmin_gate_status()
    line = ap.status_line(now, _dt(st.get("last_success_at")), _dt(st.get("fail_since")),
                          int(st.get("fail_count") or 0))
    if st.get("disabled_file"):
        line += " · ★킬스위치 켜짐★"
    elif st.get("blocked_sec_left"):
        line += f" · 차단 {int(st['blocked_sec_left'] // 60)}분 남음"
    return line


DATA_DIR = Path(__file__).parent / "data"


class GarminClient:
    def __init__(self):
        self.api = None

    def connect(self, prompt_mfa=None) -> None:
        """토큰 캐시 우선, 없으면 이메일/패스워드로 로그인.
        클라우드 환경에서는 GARMIN_TOKEN_B64 환경변수 사용."""
        import base64
        from garminconnect import Garmin

        # ── 게이트 ① 킬스위치: data/garmin_disabled 가 있으면 무조건 멈춘다 ──
        if _DISABLED_PATH.exists():
            raise GarminBlocked("data/garmin_disabled 파일이 있다 (사람이 해제해야 함)")

        # ── 게이트 ② 백오프: 차단 기간이면 호출하지 않는다 (디스크 상태라 재기동에도 유지) ──
        _gate = _load_gate()
        _left = _gate.get("blocked_until", 0) - time.time()
        if _left > 0:
            raise GarminBlocked(f"백오프 중 — {int(_left // 60)}분 남음")

        email = os.getenv("GARMIN_EMAIL", "")
        password = os.getenv("GARMIN_PASSWORD", "")

        # ① 클라우드: GARMIN_TOKEN_B64 환경변수로 토큰 복원
        token_b64 = os.getenv("GARMIN_TOKEN_B64", "")
        if token_b64:
            token_json = base64.b64decode(token_b64).decode()
            GARTH_HOME.mkdir(exist_ok=True)
            (GARTH_HOME / "garmin_tokens.json").write_text(token_json)
            log.info("GARMIN_TOKEN_B64 → 토큰 복원")

        # ② 로컬 파일 토큰으로 로그인 시도
        if GARTH_HOME.exists():
            try:
                self.api = Garmin()
                self.api.login(tokenstore=str(GARTH_HOME))
                if _gate.get("fail_count"):
                    _save_gate({"blocked_until": 0.0, "fail_count": 0,
                                "last_relogin": _gate.get("last_relogin", 0)})
                log.info("Garmin 토큰 로그인 성공")
                return
            except Exception as e:
                # ★여기가 2026-09-16 장애의 원인이었다★
                #   모든 예외를 "토큰 만료"로 읽고 곧바로 ③ 비밀번호 로그인으로 넘어갔다.
                #   429 도 이 경로를 타서 재로그인 폭주가 됐다. 이제는 ★쿨다운★ 을 본다.
                _since = time.time() - _gate.get("last_relogin", 0)
                _n = min(int(_gate.get("fail_count", 0)), len(_BACKOFF_LADDER) - 1)
                _cool = _BACKOFF_LADDER[_n]
                if _since < _cool:
                    _block(f"토큰 로그인 실패, 재로그인 쿨다운 {int(_cool // 60)}분 미경과")
                    raise GarminBlocked(
                        f"토큰 로그인 실패({e}) — 재로그인 쿨다운 중, "
                        f"{int((_cool - _since) // 60)}분 남음"
                    ) from e
                log.info("토큰 로그인 실패 (%s) — 쿨다운 경과, 재로그인 1회 시도", e)

        # ③ 이메일/패스워드 폴백
        if not email or not password:
            raise ValueError(
                "GARMIN_EMAIL / GARMIN_PASSWORD 또는 GARMIN_TOKEN_B64 환경변수 필요.\n"
                "로컬: python setup_garmin.py 실행. 클라우드: GARMIN_TOKEN_B64 설정."
            )

        mfa = prompt_mfa or (lambda: input("🔐 Garmin MFA 코드: ").strip())
        # ★비밀번호 재로그인은 시도 자체를 먼저 기록한다★ — 실패해도 쿨다운이 걸려야
        #   폭주하지 않는다. 성공 후에만 기록하면 실패 경로가 무제한 반복된다.
        _st = _load_gate()
        _st["last_relogin"] = time.time()
        _save_gate(_st)
        try:
            self.api = Garmin(email, password, prompt_mfa=mfa)
            self.api.login(tokenstore=str(GARTH_HOME))
        except Exception as e:
            _block(f"비밀번호 로그인 실패: {e}")
            raise
        _save_gate({"blocked_until": 0.0, "fail_count": 0, "last_relogin": time.time()})
        log.info("Garmin 로그인 성공 + 토큰 저장 (게이트 초기화)")

    def sync(self, weeks: int = 10) -> tuple[int, int]:
        """Garmin 데이터 동기화 → data/ 에 JSON 저장. (활동수, 웰니스일수) 반환."""
        if not self.api:
            self.connect()

        activities = self._fetch_activities(weeks)
        wellness = self._fetch_wellness(days=weeks * 7)

        DATA_DIR.mkdir(exist_ok=True)

        # ★빈 결과로 기존 기록을 덮지 않는다.★ 수집이 전부 실패해도 빈 목록이 돌아오는데,
        #   그걸 그대로 쓰면 쌓여 있던 기록이 한 번에 사라지고 실패는 아무 데도 안 남는다.
        previous_acts = load_activities()
        if sync_policy.should_write(activities, previous_acts):
            _save(DATA_DIR / "activities.json", activities)
        else:
            log.warning("활동 저장 건너뜀 — %s", sync_policy.write_skip_reason(activities, previous_acts))
            activities = previous_acts

        previous_well = load_wellness()
        if sync_policy.should_write(wellness, previous_well):
            _save(DATA_DIR / "wellness.json", wellness)
        else:
            log.warning("웰니스 저장 건너뜀 — %s", sync_policy.write_skip_reason(wellness, previous_well))
            wellness = previous_well

        log.info("동기화 완료: 활동 %d개, 웰니스 %d일", len(activities), len(wellness))
        return len(activities), len(wellness)

    def _fetch_activities(self, weeks: int) -> list[dict]:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(weeks=weeks)
        try:
            activities = self.api.get_activities_by_date(
                start_dt.strftime("%Y-%m-%d"),
                end_dt.strftime("%Y-%m-%d"),
                "running",
            )
            return activities or []
        except Exception:
            # 폴백: 최근 200개에서 러닝만 필터
            all_acts = self.api.get_activities(0, 200) or []
            cutoff = start_dt.timestamp() * 1000
            return [
                a for a in all_acts
                if a.get("activityType", {}).get("typeKey", "") == "running"
                and a.get("beginTimestamp", 0) >= cutoff
            ]

    def quick_check(self, limit: int = 5) -> list[dict]:
        """최근 활동 N개만 빠르게 조회 (5분 폴링용, 전체 sync 불필요)."""
        if not self.api:
            self.connect()
        try:
            return self.api.get_activities(0, limit) or []
        except Exception as e:
            log.warning("quick_check 실패: %s", e)
            return []

    def upload_workout(self, workout) -> dict:
        """RunningWorkout 업로드. 반환은 Garmin 응답 dict(workoutId 포함)."""
        if not self.api:
            self.connect()
        return self.api.upload_running_workout(workout)

    def schedule_workout(self, workout_id: int | str, date_str: str) -> dict:
        """업로드된 워크아웃을 날짜(YYYY-MM-DD)에 예약한다."""
        if not self.api:
            self.connect()
        return self.api.schedule_workout(workout_id, date_str)

    def list_workouts(self, limit: int = 100) -> list[dict]:
        """워크아웃 목록. 정리(cleanup)에서만 쓴다 — 주 1회다."""
        if not self.api:
            self.connect()
        try:
            return self.api.get_workouts(0, limit) or []
        except Exception as e:
            log.warning("워크아웃 목록 조회 실패: %s", e)
            return []

    def delete_workout(self, workout_id: int | str) -> bool:
        """워크아웃 1건 삭제. 실패해도 정리 전체를 멈추지 않는다."""
        if not self.api:
            self.connect()
        try:
            self.api.delete_workout(workout_id)
            return True
        except Exception as e:
            log.warning("워크아웃 삭제 실패 (%s): %s", workout_id, e)
            return False

    def sync_wellness_today(self) -> dict:
        """오늘 + 어제 웰니스만 빠르게 조회 (아침 레디니스용)."""
        if not self.api:
            self.connect()
        result = {}
        for i in range(2):
            date_str = (datetime.now(KST) - timedelta(days=i)).strftime("%Y-%m-%d")
            try:
                stats = self.api.get_stats(date_str) or {}
                if stats:
                    result[date_str] = stats
            except Exception:
                pass
        return result

    def _fetch_wellness(self, days: int) -> list[dict]:
        """★연속 실패가 이어지면 남은 회차를 돌지 않는다.★

        이전에는 70회를 전부 두드리면서 각 실패를 그냥 삼켰다. 차단 중이면 70번을 더 때려
        차단만 깊어지고, 전부 실패해도 빈 목록이 그대로 돌아가 파일을 덮었다.
        """
        wellness: list[dict] = []
        consecutive = 0
        failures = 0
        for i in range(days):
            if sync_policy.should_stop(consecutive):
                log.warning(
                    "웰니스 수집 중단 — 연속 %d회 실패 (%d/%d일 수집됨)",
                    consecutive, len(wellness), days,
                )
                break
            date_str = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            try:
                stats = self.api.get_stats(date_str) or {}
                consecutive = 0
                if stats:
                    wellness.append({"date": date_str, **stats})
            except Exception as e:
                consecutive += 1
                failures += 1
                # ★조용히 삼키지 않는다.★ 이전에는 except: pass 라 아무 흔적이 없었다.
                log.warning("웰니스 조회 실패 (%s): %s", date_str, e)
        if failures:
            log.warning("웰니스 수집: %d일 중 %d회 실패", days, failures)
        return wellness


# ── 로컬 캐시 로드 헬퍼 ──────────────────────────────────────────────────────

def load_activities() -> list[dict]:
    return _apply_overrides(_load(DATA_DIR / "activities.json", []))


def _apply_overrides(acts: list[dict]) -> list[dict]:
    """data/overrides.json의 수동 보정을 읽을 때마다 적용 — Garmin 재동기화 후에도 유지된다.

    형식: {"remove": [activityId, ...], "patch": {"activityId": {필드: 값, ...}}}
    """
    ov = _load(DATA_DIR / "overrides.json", {})
    if not ov:
        return acts

    remove = {int(i) for i in ov.get("remove", [])}
    patch = {int(k): v for k, v in ov.get("patch", {}).items()}

    result = []
    for a in acts:
        aid = a.get("activityId")
        if aid in remove:
            continue
        if aid in patch:
            a = {**a, **patch[aid]}
        result.append(a)
    return result


def load_wellness() -> list[dict]:
    return _load(DATA_DIR / "wellness.json", [])


def load_hr_zones() -> dict:
    """activityId(str) → 심박존 체류 시간 목록. sync 덮어쓰기와 무관하게 별도 보관."""
    return _load(DATA_DIR / "hr_zones.json", {})


def save_hr_zones(zones: dict) -> None:
    _save(DATA_DIR / "hr_zones.json", zones)


# ★activities.json 과 반드시 분리한다.★ sync(weeks=10) 이 activities.json 을 통째로
# 덮어쓰기 때문에, 우리가 올린 워크아웃 기록을 거기 두면 동기화 한 번에 사라진다.
WORKOUT_KEYS = ("workout_id", "name", "date", "spec", "scheduled")


def load_workouts() -> list[dict]:
    """우리가 올린 워크아웃 기록. data/ 는 gitignore 라 저장소에 안 들어간다."""
    return _load(DATA_DIR / "workouts.json", [])


def save_workouts(items: list[dict]) -> None:
    _save(DATA_DIR / "workouts.json", items)


def record_workout(entry: dict) -> None:
    """업로드 기록 1건 추가. 같은 workout_id 가 있으면 덮어쓴다(재시도해도 중복이 안 쌓인다)."""
    items = [w for w in load_workouts() if w.get("workout_id") != entry.get("workout_id")]
    items.append(entry)
    save_workouts(items)


# ── 워크아웃 정리 ────────────────────────────────────────────────────────────
#
# ★RC- 접두사가 곧 소유권 표시다.★ 사용자가 직접 만든 워크아웃은 접두사가 없으므로
# 정리 대상이 되지 않는다. 날짜도 Garmin 응답이 아니라 ★이름에 박힌 날짜★ 로 읽는다 —
# 응답 필드 이름에 기대면 판본이 바뀔 때 조용히 아무것도 안 지우거나 엉뚱한 걸 지운다.
#
# 업로드 경로에는 정리를 넣지 않는다. 업로드가 느려지고 실패 지점만 늘어난다.
WORKOUT_PREFIX = "RC-"
_NAME_DATE = re.compile(r"^RC-(\d{8})-")


def workouts_to_cleanup(items: list[dict], today: date, keep_days: int = 14) -> list:
    """지울 workoutId 목록을 고른다. ★순수 함수 — 네트워크를 타지 않는다.★

    · 이름이 RC- 로 시작하지 않으면 건드리지 않는다(사용자 소유)
    · 이름에서 날짜를 못 읽으면 건드리지 않는다 — 모르는 건 지우지 않는다
    · 미래 날짜는 건드리지 않는다(아직 뛰지 않은 예약)
    """
    out = []
    for item in items:
        name = str(item.get("workoutName") or "")
        if not name.startswith(WORKOUT_PREFIX):
            continue
        m = _NAME_DATE.match(name)
        if not m:
            continue
        try:
            made = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if made > today:
            continue
        if (today - made).days > keep_days:
            wid = item.get("workoutId")
            if wid is not None:
                out.append(wid)
    return out


def cleanup_workouts(client: "GarminClient", keep_days: int = 14, today: date | None = None) -> int:
    """오래된 RC- 워크아웃 정리. 반환은 지운 개수. 주 1회만 부른다."""
    items = client.list_workouts()
    targets = workouts_to_cleanup(items, today or datetime.now(KST).date(), keep_days)
    deleted = 0
    for wid in targets:
        if client.delete_workout(wid):
            deleted += 1
    if targets:
        remaining = [w for w in load_workouts() if w.get("workout_id") not in set(targets)]
        save_workouts(remaining)
    log.info("워크아웃 정리: 대상 %d건 중 %d건 삭제", len(targets), deleted)
    return deleted


def load_seen_ids() -> set[int]:
    return set(_load(DATA_DIR / "seen_activities.json", []))


def save_seen_ids(ids: set[int]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    _save(DATA_DIR / "seen_activities.json", list(ids))


def last_sync_time() -> datetime | None:
    path = DATA_DIR / "activities.json"
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime)


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default
