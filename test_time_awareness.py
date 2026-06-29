from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import time_awareness as twa


MADRID = ZoneInfo("Europe/Madrid")


def _epoch(y, m, d, hh, mm, ss):
    return datetime(y, m, d, hh, mm, ss, tzinfo=MADRID).timestamp()


def _cfg(**overrides):
    base = dict(timezone_name="Europe/Madrid")
    base.update(overrides)
    return twa.TimeAwarenessConfig(**base)


def test_prefixes_user_messages_with_stored_timestamp_and_strips_metadata():
    request = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "Hola", "timestamp": _epoch(2026, 6, 29, 7, 55, 0)},
            {"role": "assistant", "content": "Buenas"},
        ]
    }

    result = twa.rewrite_llm_request(request=request, platform="cli")

    assert result is not None
    msg = result["request"]["messages"][1]
    assert msg["content"] == "[time: 2026-06-29T07:55:00+02:00] Hola"
    assert "timestamp" not in msg
    # API-only: original request object was not mutated.
    assert request["messages"][1]["content"] == "Hola"
    assert "timestamp" in request["messages"][1]


def test_stamps_only_current_missing_timestamp_by_default(monkeypatch):
    fixed = _epoch(2026, 6, 29, 8, 0, 0)
    monkeypatch.setattr(twa.time, "time", lambda: fixed)
    request = {
        "messages": [
            {"role": "user", "content": "Old without timestamp"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Current without timestamp"},
        ]
    }

    result = twa.rewrite_llm_request(request=request, platform="cli")

    messages = result["request"]["messages"]
    assert messages[0]["content"] == "Old without timestamp"
    assert messages[2]["content"] == "[time: 2026-06-29T08:00:00+02:00] Current without timestamp"


def test_deduplicates_existing_time_prefix_and_preserves_embedded_time(monkeypatch):
    monkeypatch.setattr(twa.time, "time", lambda: _epoch(2026, 6, 29, 9, 0, 0))
    request = {
        "messages": [
            {"role": "user", "content": "[time: 2026-06-29T07:55:00+02:00] Hola"},
        ]
    }

    result = twa.rewrite_llm_request(request=request, platform="cli")

    content = result["request"]["messages"][0]["content"]
    assert content == "[time: 2026-06-29T07:55:00+02:00] Hola"
    assert len(re.findall(r"\[time:", content)) == 1


def test_multimodal_first_text_block_is_prefixed(monkeypatch):
    monkeypatch.setattr(twa.time, "time", lambda: _epoch(2026, 6, 29, 8, 0, 0))
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "file://x.png"}},
                    {"type": "text", "text": "Describe esto"},
                ],
            }
        ]
    }

    result = twa.rewrite_llm_request(request=request, platform="cli")

    parts = result["request"]["messages"][0]["content"]
    assert parts[1]["text"] == "[time: 2026-06-29T08:00:00+02:00] Describe esto"


def test_cron_platform_is_excluded(monkeypatch):
    monkeypatch.setattr(twa.time, "time", lambda: _epoch(2026, 6, 29, 8, 0, 0))
    request = {"messages": [{"role": "user", "content": "Run job"}]}

    assert twa.rewrite_llm_request(request=request, platform="cron") is None


def test_rewrites_responses_input_shape(monkeypatch):
    monkeypatch.setattr(twa.time, "time", lambda: _epoch(2026, 6, 29, 8, 0, 0))
    request = {"input": [{"role": "user", "content": "Hola"}]}

    result = twa.rewrite_llm_request(request=request, platform="cli")

    assert result["request"]["input"][0]["content"] == "[time: 2026-06-29T08:00:00+02:00] Hola"
