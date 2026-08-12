# 트레드밀 실측값을 data/overrides.json에 보정으로 추가하는 CLI
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import analytics as an
from garmin_client import load_activities

OV_PATH = Path(__file__).parent / "data" / "overrides.json"


def main() -> None:
    p = argparse.ArgumentParser(description="트레드밀 실측값으로 활동 보정")
    p.add_argument("--id", type=int, required=True, help="activityId")
    p.add_argument("--km", type=float, required=True, help="트레드밀 표시 거리(km)")
    p.add_argument("--min", type=float, required=True, help="트레드밀 표시 시간(분)")
    p.add_argument("--kcal", type=float, help="트레드밀 표시 칼로리")
    p.add_argument("--name", help="활동 이름")
    args = p.parse_args()

    before = next(
        (a for a in load_activities() if a.get("activityId") == args.id), None
    )
    if before is None:
        sys.exit(f"활동 {args.id} 없음")

    dist_m = args.km * 1000
    dur_s = args.min * 60

    fields = {
        "distance": dist_m,
        "duration": dur_s,
        "movingDuration": dur_s,
        "averageSpeed": dist_m / dur_s,
    }
    if args.kcal:
        fields["calories"] = args.kcal
    if args.name:
        fields["activityName"] = args.name

    ov = json.loads(OV_PATH.read_text()) if OV_PATH.exists() else {}
    ov.setdefault("remove", [])
    ov.setdefault("patch", {})
    ov["patch"].setdefault(str(args.id), {}).update(fields)
    OV_PATH.write_text(json.dumps(ov, ensure_ascii=False, indent=2))

    old_d = (before.get("distance") or 0) / 1000
    old_t = (before.get("duration") or 0) / 60
    gap = (args.km - old_d) / old_d * 100 if old_d else 0
    print(f"보정 완료 — {args.id}")
    print(f"  이전 {old_d:.2f}km / {old_t:.1f}분 → {an.format_pace(old_t * 60 / old_d)}")
    print(f"  이후 {args.km:.2f}km / {args.min:.1f}분 → {an.format_pace(dur_s / args.km)}")
    print(f"  시계 과소 {gap:+.1f}%")


if __name__ == "__main__":
    main()
