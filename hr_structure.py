"""랩 단위 심박 구조 — 평균심박 한 숫자가 가리는 것을 드러낸다.

인터벌은 평균심박이 회복 구간과 섞여 뭉개진다. 그래서 반복별로 본다:
  · 완수 — 계획 반복 수 대비 실제 ACTIVE 랩 수
  · 페이스 — 움직인 시간 기준(정지 시간 제외). 정지가 길면 따로 표시
  · 피크 심박 — ★ACTIVE 랩과 바로 뒤 RECOVERY 랩 중 큰 쪽★. 심박은 늦게 올라서
    실측에서 피크가 회복 랩에 찍힌다(2026-09-24 런: 반복 5회 중 4회)
  · 회복 — RECOVERY 랩 평균심박
지속주는 전반/후반 페이스 대비 심박 비(디커플링)를 본다. 5% 를 넘으면 유산소 기반이
그 시간을 못 버틴다는 신호다.

입력은 get_activity_splits 의 lapDTOs 를 compact_laps 로 줄인 것이다 — 좌표는 버린다.
"""
from __future__ import annotations

_KEEP = ("intensityType", "distance", "duration", "movingDuration", "averageHR", "maxHR")
_STOP_SEC = 15          # 이보다 길게 멈췄으면 정지 표시
_DECOUPLE_WARN = 5.0    # %


def compact_laps(splits: dict) -> list[dict]:
    """API 응답 → 분석에 필요한 숫자만. 위치·센서 필드는 저장하지 않는다."""
    return [{k: lap.get(k) for k in _KEEP} for lap in (splits or {}).get("lapDTOs") or []]


def _pace(dist_m: float, sec: float) -> str:
    if not dist_m or not sec:
        return "?"
    p = sec / (dist_m / 1000)
    return f"{int(p // 60)}:{int(p % 60):02d}"


def planned_reps(spec: dict | None) -> int:
    """스펙의 repeat 안에 interval 스텝이 있으면 그 반복 수. 없으면 0."""
    total = 0
    for step in (spec or {}).get("steps") or []:
        if step.get("type") == "repeat" and any(
            s.get("type") == "interval" for s in step.get("steps") or []
        ):
            total += int(step.get("iterations", 1))
    return total


def _interval_lines(laps: list[dict], planned: int) -> list[str]:
    reps = []
    for i, lap in enumerate(laps):
        if lap.get("intensityType") != "ACTIVE":
            continue
        nxt = laps[i + 1] if i + 1 < len(laps) else None
        rec = nxt if nxt and nxt.get("intensityType") == "RECOVERY" else None
        moving = lap.get("movingDuration") or lap.get("duration") or 0
        peaks = [x for x in (lap.get("maxHR"), rec and rec.get("maxHR")) if x]
        reps.append({
            "pace": _pace(lap.get("distance") or 0, moving),
            "stopped": (lap.get("duration") or 0) - moving > _STOP_SEC,
            "peak": max(peaks) if peaks else None,
            "rec_hr": rec.get("averageHR") if rec else None,
        })
    if not reps:
        return []

    n = len(reps)
    head = f"인터벌 {n}회" + (f" / 계획 {planned}회" + (" (미완수)" if n < planned else "") if planned else "")
    out = [head]
    out.append("반복 페이스: " + " / ".join(r["pace"] + ("*" if r["stopped"] else "") for r in reps)
               + (" (*반복 중 정지)" if any(r["stopped"] for r in reps) else ""))
    pk = [r["peak"] for r in reps if r["peak"]]
    if pk:
        out.append("반복 피크심박: " + " / ".join(str(round(p)) for p in pk)
                   + (f" (첫→끝 {round(pk[-1] - pk[0]):+d})" if len(pk) >= 2 else ""))
    rh = [r["rec_hr"] for r in reps if r["rec_hr"]]
    if rh:
        out.append("회복구간 평균심박: " + " / ".join(str(round(h)) for h in rh))
    return out


def _steady_lines(laps: list[dict]) -> list[str]:
    body = [l for l in laps if l.get("intensityType") not in ("WARMUP", "COOLDOWN", "RECOVERY", "REST")
            and l.get("averageHR") and (l.get("movingDuration") or l.get("duration"))]
    if len(body) < 4:
        return []
    half = len(body) // 2

    def eff(part):  # 심박 1회당 이동 거리(m) — 클수록 효율적
        sec = sum(l.get("movingDuration") or l.get("duration") for l in part)
        dist = sum(l.get("distance") or 0 for l in part)
        hr = sum(l["averageHR"] * (l.get("movingDuration") or l.get("duration")) for l in part) / sec
        return dist / sec / hr, dist, sec, hr

    e1, d1, s1, h1 = eff(body[:half])
    e2, d2, s2, h2 = eff(body[half:])
    dec = (e1 / e2 - 1) * 100
    line = (f"전반 {_pace(d1, s1)} @ {round(h1)}bpm → 후반 {_pace(d2, s2)} @ {round(h2)}bpm, "
            f"디커플링 {dec:+.1f}%")
    if dec > _DECOUPLE_WARN:
        line += " (후반 심박 상승 큼 — 유산소 지속력 한계)"
    return [line]


def hr_structure_lines(laps: list[dict], spec: dict | None = None) -> list[str]:
    """랩 → 코치 컨텍스트용 줄 목록. 판단할 근거가 없으면 빈 목록."""
    if not laps:
        return []
    types = {l.get("intensityType") for l in laps}
    if "ACTIVE" in types and "RECOVERY" in types:
        return _interval_lines(laps, planned_reps(spec))
    return _steady_lines(laps)
