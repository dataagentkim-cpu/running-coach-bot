# Garmin Connect 인증 + 러닝 활동/웰니스 데이터 수집 클라이언트
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

GARTH_HOME = Path.home() / ".garth_runcoach"
DATA_DIR = Path(__file__).parent / "data"


class GarminClient:
    def __init__(self):
        self.api = None

    def connect(self, prompt_mfa=None) -> None:
        """토큰 캐시 우선, 없으면 이메일/패스워드로 로그인.
        클라우드 환경에서는 GARMIN_TOKEN_B64 환경변수 사용."""
        import base64
        from garminconnect import Garmin

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
                log.info("Garmin 토큰 로그인 성공")
                return
            except Exception as e:
                log.info("토큰 만료 (%s), 재로그인", e)

        # ③ 이메일/패스워드 폴백
        if not email or not password:
            raise ValueError(
                "GARMIN_EMAIL / GARMIN_PASSWORD 또는 GARMIN_TOKEN_B64 환경변수 필요.\n"
                "로컬: python setup_garmin.py 실행. 클라우드: GARMIN_TOKEN_B64 설정."
            )

        mfa = prompt_mfa or (lambda: input("🔐 Garmin MFA 코드: ").strip())
        self.api = Garmin(email, password, prompt_mfa=mfa)
        self.api.login(tokenstore=str(GARTH_HOME))
        log.info("Garmin 로그인 성공 + 토큰 저장")

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

    def sync_wellness_today(self) -> dict:
        """오늘 + 어제 웰니스만 빠르게 조회 (아침 레디니스용)."""
        if not self.api:
            self.connect()
        result = {}
        for i in range(2):
            date_str = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
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
    return _load(DATA_DIR / "activities.json", [])


def load_wellness() -> list[dict]:
    return _load(DATA_DIR / "wellness.json", [])


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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default
