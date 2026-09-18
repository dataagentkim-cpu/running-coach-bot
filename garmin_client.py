# Garmin Connect 인증 + 러닝 활동/웰니스 데이터 수집 클라이언트
from __future__ import annotations

import json
import logging
import os
import time
import pytz
from datetime import datetime, timedelta
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


def _load_gate() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {"blocked_until": 0.0, "fail_count": 0, "last_relogin": 0.0}


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
    """사람이 직접 푼다."""
    _save_gate({"blocked_until": 0.0, "fail_count": 0, "last_relogin": 0.0})
    log.info("Garmin 게이트 해제됨")

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
        _save(DATA_DIR / "activities.json", activities)
        _save(DATA_DIR / "wellness.json", wellness)

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

    def fetch_hr_zones(self, activity_id: int) -> list[dict] | None:
        """활동의 심박존별 체류 시간 조회. 실패 시 None."""
        if not self.api:
            self.connect()
        try:
            return self.api.get_activity_hr_in_timezones(activity_id) or None
        except Exception as e:
            log.warning("심박존 조회 실패 (%s): %s", activity_id, e)
            return None

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
        wellness = []
        for i in range(days):
            date_str = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            try:
                stats = self.api.get_stats(date_str) or {}
                if stats:
                    wellness.append({"date": date_str, **stats})
            except Exception:
                pass
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
