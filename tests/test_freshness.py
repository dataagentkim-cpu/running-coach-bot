# 신선도 표시 시험 — ★시각 주입. 파일도 네트워크도 없다.★
#
# 막으려는 것: 캐시로 답하면서 그 사실을 안 밝히는 것. 이틀 동안 09-06 자 데이터로
# 그럴듯한 아침 메시지가 나갔고 형태가 비슷해서 아무도 몰랐다.
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import freshness as fr  # noqa: E402

NOW = datetime(2026, 9, 18, 7, 0, 0)


@pytest.mark.parametrize("delta,expected", [
    (timedelta(minutes=5), "5분 전"),
    (timedelta(hours=3), "3시간 전"),
    (timedelta(days=2), "2일 전"),
])
def test_경과시간_표기(delta, expected):
    assert fr.age_text(NOW, NOW - delta) == expected


def test_동기화_기록이_없으면_그렇게_말한다():
    assert "없음" in fr.age_text(NOW, None)


@pytest.mark.parametrize("delta,stale", [
    (timedelta(hours=1), False),
    (timedelta(hours=47), False),
    (timedelta(hours=49), True),
])
def test_낡음_경계(delta, stale):
    assert fr.is_stale(NOW, NOW - delta) is stale


def test_기록없음은_낡은것으로_본다():
    assert fr.is_stale(NOW, None) is True


# ── 한 줄 만들기 ─────────────────────────────────────────────────────────────

def test_신선하고_빠진게_없으면_줄이_없다():
    # 멀쩡할 때 매번 붙이면 소음이 되고, 소음이 되면 정작 필요할 때 안 읽는다.
    assert fr.line(NOW, NOW - timedelta(hours=2), "2026-09-18") is None


def test_낡으면_밝힌다():
    line = fr.line(NOW, NOW - timedelta(days=3))
    assert line and "낡았어" in line and "3일 전" in line


def test_웰니스_기준일을_적는다():
    # ★"09-06 기준" 이 핵심이다.★ 이게 없어서 이틀을 놓쳤다.
    line = fr.line(NOW, NOW - timedelta(hours=1), "2026-09-06")
    assert line and "09-06 기준" in line


def test_오늘_웰니스는_안_적는다():
    assert fr.line(NOW, NOW - timedelta(hours=1), "2026-09-18") is None


def test_빠진_항목을_이름으로_적는다():
    # ★조용히 빼지 않는다.★
    line = fr.line(NOW, NOW - timedelta(hours=1), "2026-09-18", ["웰니스"])
    assert line and "웰니스 없이 보냄" in line


def test_여러_사유가_한_줄에_모인다():
    line = fr.line(NOW, NOW - timedelta(days=3), "2026-09-06", ["웰니스"])
    assert line.count("·") >= 2


def test_못읽는_날짜는_밝히는_쪽으로_기운다():
    # 모르면 숨기지 않는다 — 숨기는 쪽이 이번 장애의 형태다.
    line = fr.line(NOW, NOW - timedelta(hours=1), "알 수 없음")
    assert line is not None


# ── 공허한 통과 방지 ─────────────────────────────────────────────────────────

def test_line_이_양쪽_답을_모두_낸다():
    # 항상 None 이어도 위 단정 일부가 통과한다.
    got = {fr.line(NOW, NOW - timedelta(hours=1), "2026-09-18") is None,
           fr.line(NOW, NOW - timedelta(days=5)) is None}
    assert got == {True, False}


def test_모듈이_파일을_건드리지_않는다():
    src = (ROOT / "freshness.py").read_text(encoding="utf-8")
    for banned in ("open(", "Path(", "read_text", "write_text", "stat("):
        assert banned not in src, f"freshness 가 {banned} 를 쓴다 — 순수해야 한다"
