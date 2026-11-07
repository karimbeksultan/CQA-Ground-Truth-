import json
from datetime import datetime, timezone

import httpx
import pytest

from cqa import evaluators
from cqa.llm import LLMError, Message, ModelMismatch, QuotaExhausted
from cqa.llm import openai_compat
from cqa.llm.openai_compat import OpenAICompatClient, retry_after_seconds

MSGS = [Message("user", "u"), Message("model", "prev"), Message("user", "fix it")]


def _client(handler, **kw):
    return OpenAICompatClient("m-1", "https://x.invalid/v1", kw.pop("key", "SECRET-KEY"), provider="test",
                              transport=httpx.MockTransport(handler), sleep=kw.pop("sleep", lambda s: None), **kw)


def _ok(content, usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                     "usage": usage or {}})


def test_request_shape_key_in_header_and_usage():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return _ok('{"criteria": []}', {"prompt_tokens": 11, "completion_tokens": 7,
                                        "completion_tokens_details": {"reasoning_tokens": 3}})

    reply = _client(handler, temperature=0.0).generate("SYS", MSGS)
    assert seen["url"] == "https://x.invalid/v1/chat/completions"
    assert seen["auth"] == "Bearer SECRET-KEY" and "SECRET" not in seen["url"]
    body = seen["body"]
    assert body["model"] == "m-1" and body["temperature"] == 0.0
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["response_format"] == {"type": "json_object"}
    assert reply.usage == {"prompt_tokens": 11, "output_tokens": 7, "thinking_tokens": 3}


def test_keyless_local_server_sends_no_auth_and_json_mode_can_be_off():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return _ok("{}")

    _client(handler, key=None, json_mode=False).generate("s", MSGS)
    assert seen["auth"] is None and "response_format" not in seen["body"]


def test_think_blocks_are_stripped():
    reply = _client(lambda r: _ok('<think>{"criteria": "draft"}</think>\n{"criteria": []}')).generate("s", MSGS)
    assert reply.text == '{"criteria": []}'


def test_retry_after_is_honoured_then_succeeds():
    calls, delays = [], []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": {"message": "rate limit per minute"}})
        return _ok("{}")

    assert _client(handler, sleep=delays.append).generate("s", MSGS).text == "{}"
    assert len(calls) == 2 and 5 <= delays[0] <= 7


def test_daily_limit_raises_quota_exhausted_without_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit reached: requests per day (RPD)"}})

    with pytest.raises(QuotaExhausted):
        _client(handler).generate("s", MSGS)
    assert len(calls) == 1


def test_errors_never_contain_the_key_and_non_retryable_stops():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(LLMError) as exc:
        _client(handler).generate("s", MSGS)
    assert "SECRET" not in str(exc.value) and "HTTP 401" in str(exc.value) and len(calls) == 1


def test_served_model_is_checked_and_recorded():
    def other(req):
        return httpx.Response(200, json={"model": "some-other-model", "choices": [{"message": {"content": "{}"}}]})

    with pytest.raises(ModelMismatch):
        _client(other).generate("s", MSGS)

    def same(req):
        return httpx.Response(200, json={"model": "vendor/M-1:free", "choices": [{"message": {"content": "{}"}}]})

    assert _client(same).generate("s", MSGS).served_model == "vendor/M-1:free"


def test_long_retry_after_means_daily_quota_and_plain_rate_limit_does_not():
    def long_wait(req):
        return httpx.Response(429, headers={"retry-after": "86000"}, json={"error": {"message": "rate limited"}})

    with pytest.raises(QuotaExhausted):
        _client(long_wait).generate("s", MSGS)

    calls = []

    def tpm(req):
        calls.append(1)
        if len(calls) == 1:  # mentions "daily" only in passing; per-minute limit, must be retried
            return httpx.Response(429, json={"error": {"message": "Rate limit reached for tokens per minute (TPM). "
                                                                  "Upgrade for higher daily limits"}})
        return _ok("{}")

    assert _client(tpm).generate("s", MSGS).text == "{}" and len(calls) == 2


def test_retry_after_parsing():
    now = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)
    assert retry_after_seconds("5") == 5.0
    assert retry_after_seconds("Sat, 07 Nov 2026 12:00:30 GMT", now) == 30.0
    assert retry_after_seconds("Sat, 07 Nov 2026 11:00:00 GMT", now) == 0.0
    assert retry_after_seconds("-3") == 0.0
    assert retry_after_seconds("nan") is None and retry_after_seconds("soon") is None and retry_after_seconds(None) is None


def test_json_mode_rejection_points_to_the_flag():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "response_format json_object is not supported"}})

    with pytest.raises(LLMError, match="--no-json-mode"):
        _client(handler).generate("s", MSGS)


def test_content_parts_null_message_and_non_leading_think():
    parts = _client(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": [
        {"type": "text", "text": '{"a": '}, {"type": "text", "text": "1}"}]}}]})).generate("s", MSGS)
    assert parts.text == '{"a": 1}'
    with pytest.raises(LLMError, match="empty response"):
        _client(lambda r: httpx.Response(200, json={"choices": [{"message": None}]})).generate("s", MSGS)
    kept = _client(lambda r: _ok('{"explanation": "оператор написал <think>x</think>"}')).generate("s", MSGS)
    assert "<think>x</think>" in kept.text


def test_connection_errors_are_retried():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=req)
        return _ok("{}")

    assert _client(handler).generate("s", MSGS).text == "{}" and len(calls) == 2


def test_model_is_required():
    with pytest.raises(LLMError, match="--model"):
        OpenAICompatClient("", "https://x.invalid/v1", None)


def test_providers_registered_and_missing_key_is_reported(monkeypatch):
    for name in ("groq", "cerebras", "openrouter", "mistral", "ollama", "freellmapi"):
        assert name in evaluators.available()
    monkeypatch.setattr(openai_compat, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        evaluators.create("groq", model="any")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.0.0.5:11434/v1/")
    judge = evaluators.create("ollama", model="qwen3:1.7b")
    assert judge.model == "qwen3:1.7b" and judge.client._url == "http://10.0.0.5:11434/v1/chat/completions"
