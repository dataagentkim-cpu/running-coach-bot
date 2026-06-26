# 러닝 코치 에이전트 진입점
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("runcoach")


def cmd_sync(weeks: int) -> None:
    from garmin_client import GarminClient
    log.info("Garmin 동기화 시작 (최근 %d주)", weeks)
    client = GarminClient()
    acts, well = client.sync(weeks=weeks)
    print(f"✅ 동기화 완료 — 활동 {acts}개, 웰니스 {well}일")


def cmd_status() -> None:
    from garmin_client import load_activities, load_wellness
    from coach import RunningCoach
    acts = load_activities()
    well = load_wellness()
    if not acts:
        print("데이터 없음. python main.py sync 먼저 실행해줘.")
        sys.exit(1)
    print(RunningCoach().status_report(acts, well))


def cmd_bot() -> None:
    from bot import run_bot
    run_bot()


def main() -> None:
    parser = argparse.ArgumentParser(description="러닝 코치 에이전트")
    sub = parser.add_subparsers(dest="cmd")

    p_sync = sub.add_parser("sync", help="Garmin 데이터 동기화")
    p_sync.add_argument("--weeks", type=int, default=10, help="몇 주치 (기본 10)")

    sub.add_parser("status", help="현재 피트니스 현황 출력")
    sub.add_parser("bot", help="텔레그램 봇 실행 (기본값)")

    args = parser.parse_args()
    cmd = args.cmd or "bot"

    if cmd == "sync":
        cmd_sync(args.weeks)
    elif cmd == "status":
        cmd_status()
    else:
        cmd_bot()


if __name__ == "__main__":
    main()
