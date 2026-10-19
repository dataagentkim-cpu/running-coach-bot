"""초 단위 기록 흐름 분석. 합성 데이터 — 실제 기록은 저장소에 두지 않는다."""
from hr_flow import compact_stream, flow_lines


def _build(phases, step=5):
    """phases: [(초, 속도m/s, 시작심박, 끝심박)] → [[t, 누적거리, 심박, 속도]]"""
    out, t, d = [], 0, 0.0
    for dur, v, h0, h1 in phases:
        for k in range(0, dur, step):
            out.append([t, round(d, 1), round(h0 + (h1 - h0) * k / dur), v])
            t += step
            d += v * step
    return out


FREE_INTERVAL = _build(
    [(600, 2.5, 110, 150)]
    + [(60, 3.6, 155, 172), (60, 2.2, 172, 156)] * 6
    + [(60, 2.6, 156, 160), (480, 3.3, 175, 192), (240, 2.0, 180, 140)]
)


def test_free_interval_flow_line():
    lines = flow_lines(FREE_INTERVAL)
    flow = next(l for l in lines if l.startswith("흐름:"))
    assert flow == "흐름: 워밍업 10분 → 질주 6회(평균 61초) → 템포 8분(5:06) → 쿨다운 4분"


def test_surge_rows_peak_includes_lag_and_recovery_drop():
    row = next(l for l in flow_lines(FREE_INTERVAL) if l.startswith("질주("))
    parts = row.split(": ", 1)[1].split(" / ")
    assert len(parts) == 6
    # 피크는 질주 끝 뒤 30초까지 본다(합성 데이터는 회복 시작이 172) / 1분 뒤엔 내려와 있다.
    # 페이스는 스무딩 경계가 섞여 실제 4:38 보다 조금 느리게 나온다
    assert parts[0] == "10분 65초 4:47·172·-14"


def test_tempo_drift_reported():
    tempo = next(l for l in flow_lines(FREE_INTERVAL) if l.startswith("템포 "))
    assert "페이스 5:03 → 5:03" in tempo
    assert "심박 177 → 190 (+12)" in tempo


def test_planned_reps_compared():
    lines = flow_lines(FREE_INTERVAL, planned=8)
    assert "질주 6회 / 계획 8회 (미완수)" in lines


def test_even_run_uses_decoupling_not_surges():
    lines = flow_lines(_build([(2400, 3.0, 140, 160)]))
    assert not any(l.startswith("질주") for l in lines)
    steady = next(l for l in lines if l.startswith("고른 페이스"))
    assert "유산소 지속력 한계" in steady


def test_even_flat_hr_no_warning():
    steady = next(l for l in flow_lines(_build([(2400, 3.0, 145, 146)])) if l.startswith("고른 페이스"))
    assert "한계" not in steady


def test_peak_hr_line_first():
    assert flow_lines(FREE_INTERVAL)[0].startswith("최고심박 192")


def test_short_or_empty_is_silent():
    assert flow_lines([]) == []
    assert flow_lines(_build([(200, 3.0, 140, 150)])) == []


def test_compact_stream_downsamples_and_drops_extra():
    desc = [{"key": k, "metricsIndex": i} for i, k in enumerate(
        ["directLatitude", "sumDuration", "sumDistance", "directHeartRate", "directSpeed"])]
    rows = [{"metrics": [37.5, t, t * 3.0, 150, 3.0]} for t in range(0, 20)]
    out = compact_stream({"metricDescriptors": desc, "activityDetailMetrics": rows})
    assert [r[0] for r in out] == [0, 5, 10, 15]
    assert out[1] == [5, 15.0, 150, 3.0]
    assert compact_stream({}) == [] and compact_stream(None) == []
