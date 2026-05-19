"""OllamaLLMClient — calls a local Ollama server for text generation (JSON mode)."""
from __future__ import annotations

import json
import logging
import os

import requests

from config.settings import EmbeddingConfig

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:11434"
_LLM_MODEL = "qwen2.5:1.5b"


class LLMError(Exception):
    """Raised when Ollama returns an error or is unreachable."""


class OllamaLLMClient:
    """Thin HTTP client for Ollama /api/generate in JSON mode."""

    def __init__(self, embedding_cfg: EmbeddingConfig) -> None:
        # Reuse the same Ollama server already active for embeddings (D-05).
        # endpoint resolution mirrors OllamaEmbeddingAdapter exactly.
        base = (embedding_cfg.endpoint or os.getenv("OLLAMA_ENDPOINT") or _DEFAULT_ENDPOINT).rstrip("/")
        self._generate_url = f"{base}/api/generate"

    def generate(self, prompt: str) -> dict:
        """Send prompt to Ollama /api/generate with format=json; return parsed dict.

        Raises LLMError on connection failure, bad HTTP status, or unparseable response.
        """
        try:
            response = requests.post(
                self._generate_url,
                json={
                    "model": _LLM_MODEL,
                    "prompt": prompt,
                    "format": "json",
                    "stream": False,
                },
                timeout=120,
            )
        except requests.exceptions.ConnectionError as exc:
            raise LLMError(
                f"Cannot connect to Ollama at {self._generate_url}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        if not response.ok:
            raise LLMError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        raw = data.get("response")
        if not raw:
            raise LLMError(f"Ollama response missing 'response' key: {data}")

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ollama response is not valid JSON: {raw[:200]}") from exc
