"""LLM factory for SLA Explore — OpenAI chat, same invoke shape as legacy LangChain usage."""
import os
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI

# Load Backend_Bainocular_FastAPI/.env (same pattern as self_service_api)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set (add to .env or environment)")
        _client = OpenAI(api_key=api_key)
    return _client


class _InvokeResult:
    __slots__ = ("content",)

    def __init__(self, content: str) -> None:
        self.content = content


class _LangChainStyleLLM:
    """Minimal .invoke(prompt) -> .content for get_llm_analysis."""

    def __init__(self, model: str, temperature: float) -> None:
        self._model = model
        self._temperature = temperature

    def invoke(self, prompt: str) -> _InvokeResult:
        client = _get_client()
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
        )
        text = (resp.choices[0].message.content or "").strip()
        return _InvokeResult(text)


def get_llm_for_user(
    user_email: Optional[str] = None,
    temperature: float = 0.1,
    model: Optional[str] = None,
) -> Any:
    """
    Returns an object with invoke(prompt: str) -> object with .content (str).
    user_email reserved for future per-user model routing.
    """
    _ = user_email
    m = model or os.getenv("OPENAI_MODEL", "gpt-4o")
    return _LangChainStyleLLM(model=m, temperature=temperature)
