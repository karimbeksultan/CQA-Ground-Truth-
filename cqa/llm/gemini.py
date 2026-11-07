"""Google Gemini (AI Studio) client over REST.

The key travels in the x-goog-api-key header, never in the URL, so it cannot leak through
httpx error messages or logs. Retries: 429 (honours the server's retryDelay), 500/503, timeouts.
"""
from __future__ import annotations

import os
import random
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from cqa.llm import LLMError, LLMReply, Message, QuotaExhausted

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.5-flash"
RETRYABLE = {429, 500, 502, 503, 504}
MAX_DELAY = 120.0


def api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMError("GEMINI_API_KEY is not set in .env")
    return key


def _retry_delay(payload: dict) -> float | None:
    for d in payload.get("error", {}).get("details", []) or []:
        if str(d.get("@type", "")).endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


class GeminiClient:
    # temperature defaults to the model's own default: Google advises against lowering it for Gemini 3
    # models; 0 vs default is compared on the dev split (--temperature) rather than assumed.
    def __init__(self, model: str = DEFAULT_MODEL, *, temperature: float | None = None, timeout: float = 180.0,
                 max_attempts: int = 6, max_output_tokens: int = 16384, transport: httpx.BaseTransport | None = None,
                 key: str | None = None, sleep=time.sleep):
        self.model = model
        self.temperature = temperature
        self.max_attempts = max(1, max_attempts)
        self.max_output_tokens = max_output_tokens
        self._key = key or api_key()
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport)
        # gemma models on this API reject JSON mode and system instructions
        self._json_mode = not model.startswith("gemma")

    def generate(self, system: str, messages: list[Message], json_schema: dict | None = None) -> LLMReply:
        contents = [{"role": m.role, "parts": [{"text": m.text}]} for m in messages]
        cfg: dict = {"maxOutputTokens": self.max_output_tokens}
        if self.temperature is not None:
            cfg["temperature"] = self.temperature
        body: dict = {"contents": contents, "generationConfig": cfg}
        if self._json_mode:
            body["systemInstruction"] = {"parts": [{"text": system}]}
            cfg["responseMimeType"] = "application/json"
            if json_schema:
                cfg["responseSchema"] = json_schema
        else:
            contents[0]["parts"][0]["text"] = system + "\n\n" + contents[0]["parts"][0]["text"]

        url = f"{BASE_URL}/{self.model}:generateContent"
        last = ""
        attempt = 0
        for attempt in range(1, self.max_attempts + 1):
            final = attempt == self.max_attempts
            try:
                resp = self._http.post(url, headers={"x-goog-api-key": self._key}, json=body)
            except httpx.TimeoutException:
                last = "timeout"
                if not final:
                    self._sleep(self._backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                raise LLMError(f"{self.model}: network error {type(exc).__name__}") from None
            if resp.status_code == 200:
                try:
                    return self._parse(resp.json())
                except ValueError:
                    raise LLMError(f"{self.model}: HTTP 200 with a non-JSON body") from None
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            message = str(payload.get("error", {}).get("message", ""))[:200]
            last = f"HTTP {resp.status_code}: {message}"
            if resp.status_code == 429 and "PerDay" in str(payload):
                raise QuotaExhausted(f"{self.model}: daily free-tier quota exhausted ({message})")
            if resp.status_code not in RETRYABLE or final:
                break
            server = _retry_delay(payload)
            self._sleep(min(server, MAX_DELAY) + random.uniform(0, 2) if server else self._backoff(attempt))
        raise LLMError(f"{self.model}: {last} (after {attempt} attempts)")

    @staticmethod
    def _backoff(attempt: int) -> float:
        # jitter so parallel workers do not retry in lockstep
        return min(2 ** attempt, MAX_DELAY) + random.uniform(0, 2)

    def _parse(self, payload: dict) -> LLMReply:
        candidates = payload.get("candidates") or []
        if not candidates:
            reason = payload.get("promptFeedback", {}).get("blockReason")
            raise LLMError(f"{self.model}: no candidates (blockReason={reason})")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMError(f"{self.model}: empty response (finishReason={candidates[0].get('finishReason')})")
        u = payload.get("usageMetadata", {})
        usage = {"prompt_tokens": u.get("promptTokenCount", 0), "output_tokens": u.get("candidatesTokenCount", 0),
                 "thinking_tokens": u.get("thoughtsTokenCount", 0)}
        # recorded, not enforced: Gemini aliases (e.g. *-latest) legitimately report a concrete version
        return LLMReply(text=text, usage=usage, served_model=payload.get("modelVersion"))
