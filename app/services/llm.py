"""Thin wrapper around the Claude API.

Every feature that uses the model has a deterministic fallback, so the tool is
fully usable without an API key; the model only makes the output better.
"""

from __future__ import annotations

import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError
from sqlmodel import Session

from . import settings_store

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The model call failed in a way the caller should fall back from."""


class LLM:
    def __init__(self, api_key: str, model: str, effort: str = "medium", client: anthropic.Anthropic | None = None):
        self.model = model
        self.effort = effort
        self._client = client or anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=600)

    def structured(self, *, system: str, prompt: str, schema: type[T], max_tokens: int = 16000) -> T:
        """One request whose answer is validated against ``schema``."""
        try:
            response = self._client.beta.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": self.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic API key is invalid.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(f"API key lacks permission: {exc.message}") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"Model '{self.model}' not found.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Rate limited by the Anthropic API; try again shortly.") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Bad request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API.") from exc
        except (ValidationError, ValueError) as exc:
            # parse() validates before we can inspect stop_reason; a refusal or a
            # truncated answer surfaces here as malformed output.
            raise LLMError("The model declined or returned malformed output.") from exc

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens.")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("The model returned no structured output.")
        return parsed


def get_llm(session: Session) -> LLM | None:
    """The configured model client, or ``None`` when AI features are off."""
    s = settings_store.llm_settings(session)
    if not s.get("enabled", True) or not s.get("api_key"):
        return None
    return LLM(
        api_key=s["api_key"],
        model=s.get("model") or settings_store.DEFAULT_MODEL,
        effort=s.get("effort") or "medium",
    )
