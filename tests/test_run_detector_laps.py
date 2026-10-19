"""새 런 감지 → 랩 1회 조회 → 심박 구조가 코치 프롬프트에 실린다."""
import asyncio
import time

import garmin_client
import scheduler
from tests.test_hr_structure import INTERVAL


class _FakeClient:
    calls = []

    def quick_check(self, limit=5):
        return [{"activityId": 111, "activityType": {"typeKey": "running"},
                 "activityName": "RC-test", "distance": 4000, "duration": 1800,
                 "averageHR": 137, "beginTimestamp": time.time() * 1000}]

    def fetch_laps(self, aid):
        _FakeClient.calls.append(aid)
        return INTERVAL


class _FakeCoach:
    prompts = []

    def chat(self, msg, acts, well):
        _FakeCoach.prompts.append(msg)
        return "ok", None


def _run(monkeypatch, tmp_path):
    monkeypatch.setattr(garmin_client, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "GarminClient", _FakeClient)
    monkeypatch.setattr(scheduler, "_coach", _FakeCoach())
    sent = []

    async def _send(ctx, text):
        sent.append(text)

    monkeypatch.setattr(scheduler, "_send", _send)
    asyncio.run(scheduler.check_new_runs(None))
    return sent


def test_new_run_fetches_laps_once_and_feeds_prompt(monkeypatch, tmp_path):
    _FakeClient.calls.clear(); _FakeCoach.prompts.clear()
    sent = _run(monkeypatch, tmp_path)
    assert _FakeClient.calls == [111]
    assert sent and "인터벌 5회" in _FakeCoach.prompts[0]
    assert "반복 피크심박: 174" in _FakeCoach.prompts[0]
    assert garmin_client.load_laps()["111"] == INTERVAL


def test_seen_run_does_not_refetch(monkeypatch, tmp_path):
    _FakeClient.calls.clear()
    _run(monkeypatch, tmp_path)
    _run(monkeypatch, tmp_path)   # 두 번째는 seen — 조회 없음
    assert _FakeClient.calls == [111]
