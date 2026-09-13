"""Controlled LLM client for D8 Ask. Credentials stay on the server."""

from __future__ import annotations

import logging
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from dfip_analytics.trends import TREND_DIMENSIONS_BY_KEY, TREND_GRAINS, TREND_METRICS_BY_KEY
from dfip_config.settings import Settings

log = logging.getLogger(__name__)

OPENAI_DEFAULT_API_BASE = "https://api.openai.com/v1"
GEMINI_DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_WORDING_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "user_claim": {"type": "STRING", "enum": ["fall", "rise", "none"]},
        "direction": {
            "type": "STRING",
            "enum": ["rose", "fell", "unchanged", "unclear"],
        },
        "evidence_incomplete": {"type": "BOOLEAN"},
        "suggest_breakdown": {"type": "BOOLEAN"},
    },
    "required": [
        "user_claim",
        "direction",
        "evidence_incomplete",
        "suggest_breakdown",
    ],
}


GEMINI_INTENT_THINKING_LEVEL = "low"
GEMINI_INTENT_MAX_OUTPUT_TOKENS = 512


def gemini_ask_intent_schema() -> dict[str, Any]:
    from dfip_analytics.ask import OPERATIONS
    from dfip_analytics.explorer import EXPLORER_DIMENSIONS_BY_KEY
    from dfip_analytics.trends import TREND_METRICS_BY_KEY

    operations = [*OPERATIONS, "unsupported"]
    metrics = ["none", *TREND_METRICS_BY_KEY]
    dimensions = ["none", *EXPLORER_DIMENSIONS_BY_KEY]
    return {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "enum": operations},
            "metric": {"type": "STRING", "enum": metrics},
            "dimension": {"type": "STRING", "enum": dimensions},
        },
        "required": ["operation"],
    }


def gemini_trend_selection_schema() -> dict[str, Any]:
    metrics = list(TREND_METRICS_BY_KEY)
    return {
        "type": "OBJECT",
        "properties": {
            "metric": {"type": "STRING", "enum": metrics},
            "secondary": {"type": "STRING", "enum": ["none", *metrics]},
            "grain": {"type": "STRING", "enum": list(TREND_GRAINS)},
            "breakdown": {
                "type": "STRING",
                "enum": ["none", *TREND_DIMENSIONS_BY_KEY],
            },
            "compare": {"type": "STRING", "enum": ["omit", "none"]},
        },
        "required": ["metric", "grain"],
    }


def ask_provider_is_enabled(provider: str | None) -> bool:
    """True when Ask may call an optional wording/intent provider."""
    return (provider or "none").strip().lower() not in {"", "none"}


class AskLlmError(Exception):
    """The language-model call failed. Callers must fall back to a template."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _log_ask_llm_http(*, purpose: str | None, status: int) -> None:
    log.warning("ask-llm-http purpose=%s status=%s", purpose or "unspecified", status)


class AskLlm(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
        purpose: str | None = None,
    ) -> str: ...


class ScriptedAskLlm:
    """Test double. Never used in production."""

    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.call_kwargs: list[dict[str, Any]] = []
        self.last_kwargs: dict[str, Any] = {}

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
        purpose: str | None = None,
    ) -> str:
        self.calls.append((system, user))
        kwargs = {
            "thinking_level": thinking_level,
            "max_output_tokens": max_output_tokens,
            "response_schema": response_schema,
            "purpose": purpose,
        }
        self.call_kwargs.append(kwargs)
        self.last_kwargs = kwargs
        if self.error is not None:
            raise self.error
        if self.text is None:
            raise AskLlmError("scripted model returned no text")
        return self.text


class HttpAskLlm:
    """OpenAI-compatible chat completions. No tool/SQL calling."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_output_tokens

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
        purpose: str | None = None,
    ) -> str:
        del response_schema, thinking_level
        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": int(max_output_tokens or self._max_tokens),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise AskLlmError("language model timed out") from exc
        except httpx.HTTPError as exc:
            raise AskLlmError("language model request failed") from exc
        if response.status_code >= 400:
            _log_ask_llm_http(purpose=purpose, status=response.status_code)
            raise AskLlmError("language model request failed", status_code=response.status_code)
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AskLlmError("language model returned an invalid payload") from exc
        if not isinstance(text, str):
            raise AskLlmError("language model returned an invalid payload")
        return text.strip()


class GeminiAskLlm:
    """Gemini native generateContent wording. No tool/SQL calling."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_output_tokens

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict[str, Any] | None = None,
        thinking_level: str | None = None,
        max_output_tokens: int | None = None,
        purpose: str | None = None,
    ) -> str:
        model = self._model
        if model.startswith("models/"):
            model = model[len("models/") :]
        url = f"{self._api_base}/models/{quote(model, safe='.-_')}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        config: dict[str, Any] = {
            "temperature": 0,
            "maxOutputTokens": int(max_output_tokens or self._max_tokens),
            "responseMimeType": "application/json",
            "responseSchema": response_schema or GEMINI_WORDING_SCHEMA,
        }
        if thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": thinking_level}
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise AskLlmError("language model timed out") from exc
        except httpx.HTTPError as exc:
            raise AskLlmError("language model request failed") from exc
        if response.status_code >= 400:
            _log_ask_llm_http(purpose=purpose, status=response.status_code)
            raise AskLlmError("language model request failed", status_code=response.status_code)
        try:
            body = response.json()
        except ValueError as exc:
            raise AskLlmError("language model returned an invalid payload") from exc
        return _extract_gemini_text(body)


def _extract_gemini_text(body: object) -> str:
    if not isinstance(body, dict):
        raise AskLlmError("language model returned an invalid payload")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise AskLlmError("language model returned an invalid payload")
    first = candidates[0]
    if not isinstance(first, dict):
        raise AskLlmError("language model returned an invalid payload")
    content = first.get("content")
    if not isinstance(content, dict):
        raise AskLlmError("language model returned an invalid payload")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise AskLlmError("language model returned an invalid payload")
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise AskLlmError("language model returned an invalid payload")
        if any(key in part for key in ("functionCall", "executableCode", "codeExecutionResult")):
            raise AskLlmError("language model returned an invalid payload")
        if part.get("thought") is True:
            continue
        text = part.get("text")
        if text is None:
            continue
        if not isinstance(text, str):
            raise AskLlmError("language model returned an invalid payload")
        texts.append(text)
    combined = "".join(texts).strip()
    if not combined:
        raise AskLlmError("language model returned an invalid payload")
    return combined


def build_ask_llm(settings: Settings) -> AskLlm | None:
    provider = (settings.dfip_ask_provider or "none").strip().lower()
    api_key = settings.dfip_ask_api_key.strip()
    if not ask_provider_is_enabled(provider):
        return None
    if not api_key:
        log.warning("ask-llm-key-missing provider=%s", provider)
        return None
    timeout_seconds = float(settings.dfip_ask_timeout_seconds)
    max_output_tokens = int(settings.dfip_ask_max_output_tokens)
    if provider == "openai":
        return HttpAskLlm(
            api_key=api_key,
            api_base=settings.dfip_ask_api_base.strip() or OPENAI_DEFAULT_API_BASE,
            model=settings.dfip_ask_model.strip() or "gpt-4o-mini",
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    if provider == "gemini":
        model = settings.dfip_ask_model.strip()
        if not model:
            log.warning("ask-llm-model-missing provider=gemini")
            return None
        base = settings.dfip_ask_api_base.strip()
        if not base or base.rstrip("/") == OPENAI_DEFAULT_API_BASE:
            base = GEMINI_DEFAULT_API_BASE
        return GeminiAskLlm(
            api_key=api_key,
            api_base=base,
            model=model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    log.warning("ask-llm-provider-ignored provider=%s", provider)
    return None
