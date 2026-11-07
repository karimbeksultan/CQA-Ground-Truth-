"""Client for any OpenAI-compatible /chat/completions API: Groq, Cerebras, OpenRouter, Mistral,
a local Ollama, or a FreeLLMAPI router. The model is always pinned explicitly (--model), and the
model the API reports in its response is checked against it, so one run is graded by one model.

The key travels only in the Authorization header. Retries: 429 (honours Retry-After), 5xx,
timeouts and dropped connections; a daily limit raises QuotaExhausted so the runner stops.
"""
from __future__ import annotations

import math
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from cqa.llm import LLMError, LLMReply, Message, ModelMismatch, QuotaExhausted

ROOT = Path(__file__).resolve().parents[2]
RETRYABLE = {429, 500, 502, 503, 504}
MAX_DELAY = 120.0
DAILY_RETRY_AFTER = 3600.0  # a server asking to wait longer than this means the daily quota is gone
_LEADING_THINK = re.compile(r"\A\s*<think>.*?</think>", re.S | re.I)
_DAILY = re.compile(r"per[ _-]?day|requests per day|tokens per day|\bRPD\b|\bTPD\b", re.I)
_JSON_MODE_HINT = re.compile(r"response_format|json", re.I)

# name -> (base_url, env var holding the key or None for keyless local servers)
PROVIDERS: dict[str, tuple[str, str | None]] = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "freellmapi": ("http://localhost:3001/v1", "FREELLMAPI_API_KEY"),
}


def resolve(provider: str) -> tuple[str, str | None]:
    """Base URL and key for a provider; <PROVIDER>_BASE_URL in .env overrides the default URL."""
    if provider not in PROVIDERS:
        raise LLMError(f"unknown provider {provider!r}; known: {sorted(PROVIDERS)}")
    load_dotenv(ROOT / ".env")
    base, key_env = PROVIDERS[provider]
    base = os.environ.get(f"{provider.upper()}_BASE_URL", "").strip() or base
    if key_env is None:
        return base.rstrip("/"), None
    key = os.environ.get(key_env, "").strip()
    if not key:
        raise LLMError(f"{key_env} is not set in .env")
    return base.rstrip("/"), key


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After as seconds: delta-seconds or an HTTP date; None if absent or unusable."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def same_model(requested: str, served: str) -> bool:
    """Equal up to vendor prefix, ':free'-style variants and letter case (Groq/OpenRouter naming)."""
    def norm(name: str) -> str:
        name = name.strip().lower().rsplit("/", 1)[-1]
        return re.sub(r":(free|beta|extended|nitro|floor)$", "", name)
    return norm(requested) == norm(served)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # list of parts (Mistral, some routers)
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") == "text")
    return ""


class OpenAICompatClient:
    def __init__(self, model: str, base_url: str, key: str | None, *, provider: str = "openai-compat",
                 temperature: float | None = None, json_mode: bool = True, timeout: float = 300.0,
                 max_attempts: int = 6, max_output_tokens: int = 8192,
                 transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not model:
            raise LLMError(f"{provider}: pass --model (the model is never guessed)")
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.json_mode = json_mode
        self.max_attempts = max(1, max_attempts)
        self.max_output_tokens = max_output_tokens
        self._url = f"{base_url}/chat/completions"
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def generate(self, system: str, messages: list[Message], json_schema: dict | None = None) -> LLMReply:
        # json_schema is not sent: providers disagree on schema support; json_object + strict validation instead
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
            + [{"role": "assistant" if m.role == "model" else "user", "content": m.text} for m in messages],
            "max_tokens": self.max_output_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}

        last = ""
        attempt = 0
        for attempt in range(1, self.max_attempts + 1):
            final = attempt == self.max_attempts
            try:
                resp = self._http.post(self._url, headers=self._headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = "timeout" if isinstance(exc, httpx.TimeoutException) else f"connection error {type(exc).__name__}"
                if not final:
                    self._sleep(self._backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                raise LLMError(f"{self.provider}/{self.model}: network error {type(exc).__name__}") from None
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    raise LLMError(f"{self.provider}/{self.model}: HTTP 200 with a non-JSON body") from None
                return self._parse(payload)
            message = self._error_message(resp)
            last = f"HTTP {resp.status_code}: {message}"
            wait = retry_after_seconds(resp.headers.get("retry-after"))
            if resp.status_code == 429 and (_DAILY.search(message) or (wait or 0) > DAILY_RETRY_AFTER):
                raise QuotaExhausted(f"{self.provider}/{self.model}: daily limit reached ({message})")
            if resp.status_code == 400 and self.json_mode and _JSON_MODE_HINT.search(message):
                raise LLMError(f"{self.provider}/{self.model}: {last} — this model may not support JSON mode; "
                               "re-run with --no-json-mode")
            if resp.status_code not in RETRYABLE or final:
                break
            self._sleep(min(wait, MAX_DELAY) + random.uniform(0, 2) if wait is not None else self._backoff(attempt))
        raise LLMError(f"{self.provider}/{self.model}: {last} (after {attempt} attempts)")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2 ** attempt, MAX_DELAY) + random.uniform(0, 2)

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            err = resp.json().get("error", {})
        except (ValueError, AttributeError):
            return resp.text[:200]
        return str(err.get("message", err) if isinstance(err, dict) else err)[:200]

    def _parse(self, payload: dict) -> LLMReply:
        served = payload.get("model")
        if served and not same_model(self.model, str(served)):
            raise ModelMismatch(f"{self.provider}: requested {self.model!r} but the API answered with {served!r}")
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError(f"{self.provider}/{self.model}: no choices in response")
        message = choices[0].get("message") or {}
        text = _LEADING_THINK.sub("", _content_text(message.get("content"))).strip()
        if not text:
            raise LLMError(f"{self.provider}/{self.model}: empty response (finish_reason={choices[0].get('finish_reason')})")
        u = payload.get("usage") or {}
        details = u.get("completion_tokens_details") or {}
        usage = {"prompt_tokens": u.get("prompt_tokens", 0) or 0, "output_tokens": u.get("completion_tokens", 0) or 0,
                 "thinking_tokens": details.get("reasoning_tokens", 0) or 0}
        return LLMReply(text=text, usage=usage, served_model=str(served) if served else None)
