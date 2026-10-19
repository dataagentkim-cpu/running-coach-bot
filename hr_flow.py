"""초 단위 심박·속도 기록으로 런 전체 흐름을 읽는다 — 워크아웃이든 즉흥 런이든 똑같이.

랩은 시계가 나눠줘야만 쓸 수 있다. 즉흥으로 빠르게·천천히를 섞으면 1km 자동 랩 안에서
뭉개진다. 그래서 랩이 아니라 기록 자체에서 '빨라진 구간'을 찾는다:

  1. 속도를 20초 창으로 매끄럽게 한다 (GPS 튐 제거)
  2. 이 런 자신의 속도 분포로 빠름/느림 경계를 잡는다 — 상위·하위 사분위의 중간.
     두 사분위 차이가 작으면(< 15%) 페이스가 고른 런이다 → 질주 탐지 안 함
  3. 빠름이 30초 이상 이어지면 '질주'. 5분 이상이면 '템포'
  4. 질주마다: 페이스, 피크심박(★끝난 뒤 30초까지★ — 심박이 늦게 오른다),
     끝난 뒤 60초간 심박 하강(회복력)
  5. 템포·지속주: 첫 1/4 대비 마지막 1/4 의 심박 상승과 페이스 변화

Claude 에는 원본이 아니라 이 요약 몇 줄만 보낸다.
"""
from __future__ import annotations

from bisect import bisect_left

_SMOOTH_SEC = 20
_MIN_SURGE_SEC = 30
_TEMPO_SEC = 300
_MERGE_GAP_SEC = 30    # 신호 대기 정도의 짧은 늦춤은 같은 구간으로 본다
_SPREAD_MIN = 1.15      # 빠른 사분위 / 느린 사분위 속도 비
_MOVING = 1.0           # m/s 미만은 정지·걷기로 본다
_KEEP_COLS = ("sumDuration", "sumDistance", "directHeartRate", "directSpeed")
_STEP_SEC = 5           # 저장 간격


def compact_stream(details: dict) -> list[list[float]]:
    """get_activity_details 응답 → [[초, 누적거리m, 심박, 속도m/s], ...] 5초 간격.

    좌표·기타 센서는 버린다.
    """
    desc = {m["key"]: m["metricsIndex"] for m in (details or {}).get("metricDescriptors") or []}
    if not all(k in desc for k in _KEEP_COLS):
        return []
    out, last_t = [], -_STEP_SEC
    for r in (details or {}).get("activityDetailMetrics") or []:
        m = r.get("metrics") or []
        try:
            row = [m[desc[k]] for k in _KEEP_COLS]
        except IndexError:
            continue
        if row[0] is None or row[0] - last_t < _STEP_SEC:
            continue
        last_t = row[0]
        out.append([round(row[0]), round(row[1] or 0, 1), row[2], round(row[3] or 0, 2)])
    return out


def _fmt_pace(mps: float) -> str:
    if mps <= 0.5:
        return "-"
    p = 1000 / mps
    return f"{int(p // 60)}:{int(p % 60):02d}"


def _fmt_min(sec: float) -> str:
    return f"{sec / 60:.0f}분" if sec >= 90 else f"{sec:.0f}초"


def _smooth(ts, vs):
    out, lo, acc = [], 0, 0.0
    for i in range(len(ts)):
        acc += vs[i]
        while ts[i] - ts[lo] > _SMOOTH_SEC:
            acc -= vs[lo]
            lo += 1
        out.append(acc / (i - lo + 1))
    return out


def _quantile(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def _hr_at(ts, hrs, t):
    i = min(bisect_left(ts, t), len(ts) - 1)
    return hrs[i]


def _segments(ts, fast, vs=None, floor=0.0):
    """fast(bool 목록) → [(시작초, 끝초)], 짧은 틈은 메우고 짧은 구간은 버린다.

    틈은 ★회복 페이스(floor)까지 떨어지지 않았을 때만★ 메운다 — 신호 대기 같은 늦춤과
    진짜 회복 조깅을 가르는 기준이다.
    """
    segs, start = [], None
    for t, f in zip(ts, fast):
        if f and start is None:
            start = t
        elif not f and start is not None:
            segs.append([start, t])
            start = None
    if start is not None:
        segs.append([start, ts[-1]])
    merged = []
    for s in segs:
        if merged and s[0] - merged[-1][1] <= _MERGE_GAP_SEC and (
            vs is None or min(
                (v for t, v in zip(ts, vs) if merged[-1][1] <= t <= s[0]), default=0
            ) > floor
        ):
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(a, b) for a, b in merged if b - a >= _MIN_SURGE_SEC]


def _block_drift(ts, ds, hrs, a, b):
    """구간 [a,b] 의 첫 1/4 → 마지막 1/4 심박·페이스."""
    q = (b - a) / 4

    def part(x, y):
        i, j = bisect_left(ts, x), bisect_left(ts, y)
        if j - i < 2:
            return None, None
        h = [v for v in hrs[i:j] if v]
        spd = (ds[j - 1] - ds[i]) / max(ts[j - 1] - ts[i], 1)
        return (sum(h) / len(h) if h else None), spd

    h1, v1 = part(a, a + q)
    h2, v2 = part(b - q, b)
    return h1, v1, h2, v2


def flow_lines(stream: list[list[float]], planned: int = 0) -> list[str]:
    """요약 기록 → 코치 컨텍스트 줄. 판단 근거가 부족하면 빈 목록."""
    pts = [p for p in stream or [] if p[2]]
    if len(pts) < 60:                       # 5초 간격 5분 미만
        return []
    ts = [p[0] for p in pts]
    ds = [p[1] for p in pts]
    hrs = [p[2] for p in pts]
    vs = _smooth(ts, [p[3] for p in pts])

    peak_i = max(range(len(hrs)), key=lambda i: hrs[i])
    out = [f"최고심박 {round(hrs[peak_i])} ({ts[peak_i] / 60:.0f}분째)"]

    moving = [v for v in vs if v >= _MOVING]
    if len(moving) < 30:
        return out
    lo, hi = _quantile(moving, 0.25), _quantile(moving, 0.75)

    if hi / lo < _SPREAD_MIN:
        # 고른 페이스 — 첫 5분(워밍업)을 빼고 지속주 드리프트만 본다
        start = ts[0] + 300
        if ts[-1] - start < 600:
            return out
        h1, v1, h2, v2 = _block_drift(ts, ds, hrs, start, ts[-1])
        if h1 and h2 and v1 and v2:
            dec = ((v1 / h1) / (v2 / h2) - 1) * 100
            line = (f"고른 페이스: 초반 {_fmt_pace(v1)} @ {round(h1)} → 후반 {_fmt_pace(v2)} @ {round(h2)}, "
                    f"디커플링 {dec:+.1f}%")
            if dec > 5:
                line += " (후반 심박 상승 큼 — 유산소 지속력 한계)"
            out.append(line)
        return out

    cut = (lo + hi) / 2
    segs = _segments(ts, [v >= cut for v in vs], vs, lo)
    surges = [s for s in segs if s[1] - s[0] < _TEMPO_SEC]
    tempos = [s for s in segs if s[1] - s[0] >= _TEMPO_SEC]

    # 흐름 한 줄: 시간 순서대로
    events = []
    if segs and segs[0][0] - ts[0] >= 180:
        events.append(f"워밍업 {_fmt_min(segs[0][0] - ts[0])}")
    i = 0
    while i < len(segs):
        a, b = segs[i]
        if b - a >= _TEMPO_SEC:
            dist = ds[bisect_left(ts, b) - 1] - ds[bisect_left(ts, a)]
            events.append(f"템포 {_fmt_min(b - a)}({_fmt_pace(dist / (b - a))})")
            i += 1
            continue
        j = i
        while j + 1 < len(segs) and segs[j + 1][1] - segs[j + 1][0] < _TEMPO_SEC:
            j += 1
        run = segs[i:j + 1]
        avg = sum(b - a for a, b in run) / len(run)
        events.append(f"질주 {len(run)}회(평균 {_fmt_min(avg)})")
        i = j + 1
    if segs and ts[-1] - segs[-1][1] >= 180:
        events.append(f"쿨다운 {_fmt_min(ts[-1] - segs[-1][1])}")
    out.append("흐름: " + " → ".join(events))

    if surges:
        n = len(surges)
        if planned:
            out.append(f"질주 {n}회 / 계획 {planned}회" + (" (미완수)" if n < planned else ""))
        rows, peaks = [], []
        for a, b in surges:
            ia, ib = bisect_left(ts, a), bisect_left(ts, b)
            pace = _fmt_pace((ds[ib - 1] - ds[ia]) / max(ts[ib - 1] - ts[ia], 1))
            ip = bisect_left(ts, b + 30)
            pk = round(max(hrs[ia:max(ip, ia + 1)]))
            peaks.append(pk)
            row = f"{a / 60:.0f}분 {_fmt_min(b - a)} {pace}·{pk}"
            if b + 60 <= ts[-1]:
                row += f"·{round(_hr_at(ts, hrs, b + 60)) - pk:+d}"
            rows.append(row)
        out.append("질주(시작·길이·페이스·피크심박·1분 뒤 변화): " + " / ".join(rows))
        if n >= 2:
            out.append(f"질주 피크심박 첫→끝 {peaks[-1] - peaks[0]:+d}")

    for a, b in tempos:
        h1, v1, h2, v2 = _block_drift(ts, ds, hrs, a, b)
        if h1 and h2:
            out.append(f"템포 {a / 60:.0f}~{b / 60:.0f}분: 심박 {round(h1)} → {round(h2)} ({round(h2 - h1):+d}), "
                       f"페이스 {_fmt_pace(v1)} → {_fmt_pace(v2)}")
    return out
