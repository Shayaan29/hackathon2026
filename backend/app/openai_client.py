"""One small place that talks to the OpenAI HTTP API for text and embeddings.

The voice connection (realtime.py) is separate. This client is for the manual search
and the recommendations. It never raises anything but OpenAIError, and its messages
never contain the key: the API repeats part of a wrong key in its error text, so
anything that looks like a key is removed before a message leaves this module.
"""

from __future__ import annotations

import re
from typing import Any, Callable

import httpx

DEFAULT_BASE_URL = "https://api.openai.com/v1"
_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_*\-\.]{4,}")


class OpenAIError(RuntimeError):
    """A call to the OpenAI API failed. The message is safe to show to the person."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def redact(text: str) -> str:
    return _KEY_LIKE.sub("sk-…", text)


def _api_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except ValueError:
        message = None
    return redact(str(message or response.text or response.reason_phrase)[:300])


class OpenAIClient:
    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL,
                 factory: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self._factory = factory

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                      *, timeout: float = 60.0) -> dict[str, Any]:
        if not self._key:
            raise OpenAIError("No OpenAI key is set. Put OPENAI_API_KEY into backend/.env and restart.")
        client = self._factory() if self._factory else httpx.AsyncClient()
        try:
            async with client as http:
                response = await http.request(method, self.base_url + path, json=payload, timeout=timeout,
                                              headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException as exc:
            raise OpenAIError(f"OpenAI did not answer within {timeout:.0f} s.") from exc
        except httpx.HTTPError as exc:
            raise OpenAIError(f"Could not reach OpenAI: {redact(str(exc) or type(exc).__name__)}") from exc
        if response.status_code == 401:
            raise OpenAIError("OpenAI rejected the key. Check OPENAI_API_KEY in backend/.env.", 401)
        if response.status_code == 429:
            raise OpenAIError(f"OpenAI says: {_api_message(response)} (rate limit or no credit left).", 429)
        if response.status_code >= 400:
            raise OpenAIError(f"OpenAI error {response.status_code}: {_api_message(response)}",
                              response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise OpenAIError("OpenAI answered with something that is not JSON.") from exc
        if not isinstance(data, dict):
            raise OpenAIError("OpenAI answered with an unexpected shape.")
        return data
