# 기존 러닝 활동의 심박존 체류 시간을 1회성으로 백필하는 스크립트
from __future__ import annotations

import sys
import time

from garmin_client import (
    GarminClient,
    load_activities,
    load_hr_zones,
    save_hr_zones,
)

LIMIT = 12          # 한 번에 받을 최대 활동 수
DELAY_SEC = 4       # 호출 간 대기 — Garmin 429 방지


def main() -> None:
    cache = load_hr_zones()
    acts = load_activities()

    targets = [
        a for a in acts
        if a.get("activityId") and str(a["activityId"]) not in cache
    ][:LIMIT]

    if not targets:
        print("백필할 활동 없음")
        return

    print(f"대상 {len(targets)}개 (캐시 {len(cache)}개 보유)")
    client = GarminClient()
    client.connect()

    added = 0
    for i, act in enumerate(targets, 1):
        aid = act["activityId"]
        zones = client.fetch_hr_zones(aid)
        if zones is None:
            print(f"  [{i}/{len(targets)}] {aid} 실패 — 중단")
            break
        cache[str(aid)] = zones
        added += 1
        total = sum(z.get("secsInZone") or 0 for z in zones)
        hard = sum(
            z.get("secsInZone") or 0 for z in zones if (z.get("zoneNumber") or 0) >= 4
        )
        pct = f"{hard / total * 100:.0f}%" if total else "-"
        print(f"  [{i}/{len(targets)}] {aid} {act.get('activityName')} → 고강도 {pct}")
        if i < len(targets):
            time.sleep(DELAY_SEC)

    save_hr_zones(cache)
    print(f"\n{added}개 추가, 캐시 총 {len(cache)}개")


if __name__ == "__main__":
    sys.exit(main())
