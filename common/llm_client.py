"""
Provider-swappable LLM client for Project Phoenix demo.

The whole demo talks to ONE interface (LLMClient). Jarvis and Skynet never
know which provider is behind it. Swap Gemini <-> Claude by changing ONE line
in config (see get_llm() at the bottom), nothing else.

This is the single seam that keeps the architecture LLM-agnostic, exactly as
the v1.8 spec promises: the LLM is a brain plugged into a slot, not the system.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()



# ---------------------------------------------------------------------------
# Shared types — every provider returns these, so callers stay provider-blind
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A request from the model to call one of our tools (e.g. an MCP tool)."""
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response. Either `text` is set, or `tool_calls` is non-empty."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None  # provider-native response, for debugging


@dataclass
class ToolSpec:
    """Provider-neutral description of a tool the model may call."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema-style dict


# ---------------------------------------------------------------------------
# The interface every provider implements
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    model: str

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """messages: [{"role": "user"|"assistant", "content": "..."}]"""
        ...


# ---------------------------------------------------------------------------
# Gemini implementation (default) — uses the new `google-genai` SDK
#   pip install google-genai
#   env: GEMINI_API_KEY=...
# ---------------------------------------------------------------------------

class GeminiClient:
    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None):
        from google import genai  # imported lazily so Claude-only users don't need it

        self.model = model
        self._genai = genai
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])

    def complete(self, system, messages, tools=None, temperature=0.2) -> LLMResponse:
        from google.genai import types

        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))

        cfg_kwargs: dict[str, Any] = {
            "system_instruction": system,
            "temperature": temperature,
        }
        if tools:
            decls = [
                types.FunctionDeclaration(
                    name=t.name, description=t.description, parameters=t.parameters
                )
                for t in tools
            ]
            cfg_kwargs["tools"] = [types.Tool(function_declarations=decls)]
            # we want explicit tool-call parts back, not auto-execution
            cfg_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True
            )

        resp = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )

        tool_calls: list[ToolCall] = []
        if getattr(resp, "function_calls", None):
            for fc in resp.function_calls:
                tool_calls.append(ToolCall(name=fc.name, arguments=dict(fc.args or {})))

        return LLMResponse(text=resp.text or "", tool_calls=tool_calls, raw=resp)


# ---------------------------------------------------------------------------
# Claude implementation (swap target) — uses the `anthropic` SDK
#   pip install anthropic
#   env: ANTHROPIC_API_KEY=...
# ---------------------------------------------------------------------------

class ClaudeClient:
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    def complete(self, system, messages, tools=None, temperature=0.2) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]

        resp = self._client.messages.create(**kwargs)

        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(name=block.name, arguments=dict(block.input)))

        return LLMResponse(text="".join(text_parts), tool_calls=tool_calls, raw=resp)


# ---------------------------------------------------------------------------
# THE SWAP POINT — change provider here (or via env PHOENIX_LLM=gemini|claude)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Provider resolution (one place, used by get_llm AND the readiness checks)
# ---------------------------------------------------------------------------

def resolve_provider() -> str:
    """Which provider to use. Explicit PHOENIX_LLM wins; otherwise auto-detect
    from whichever REAL API key is present (so 'I have a key in env' just works;
    placeholder values are ignored)."""
    explicit = os.environ.get("PHOENIX_LLM")
    if explicit:
        return explicit.lower()
    if _looks_like_real_key(os.environ.get("GEMINI_API_KEY")):
        return "gemini"
    if _looks_like_real_key(os.environ.get("ANTHROPIC_API_KEY")):
        return "claude"
    return "gemini"   # default; will report 'no key' if none set


def key_env_for(provider: str) -> str:
    return "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY"


def _looks_like_real_key(val: str | None) -> bool:
    """A non-empty value that isn't an obvious placeholder from .env.example."""
    if not val:
        return False
    v = val.strip().strip("\"'").lower()
    if not v:
        return False
    placeholders = ("your_", "api_key_here", "xxxx", "<", "changeme",
                    "paste", "todo", "example", "...")
    if any(p in v for p in placeholders):
        return False
    if len(set(v)) <= 2:           # e.g. "xxxxxxxx" / "aaaa"
        return False
    return True


def llm_ready() -> tuple[bool, str, str]:
    """(real_key_present, provider, key_env_var_name) — for status + gating.
    A placeholder value (e.g. 'your_..._here', 'xxxx') counts as NOT present."""
    provider = resolve_provider()
    env_var = key_env_for(provider)
    return _looks_like_real_key(os.environ.get(env_var)), provider, env_var


def get_llm(role: str = "fast") -> LLMClient:
    """
    role="fast"  -> cheap/quick model (Jarvis intent routing)
    role="deep"  -> stronger model   (Skynet FEAS / negotiation reasoning)
    """
    provider = resolve_provider()

    if provider == "gemini":
        default_model = "gemini-2.5-flash" if role == "fast" else "gemini-2.5-pro"
        env_var = "GEMINI_FAST_MODEL" if role == "fast" else "GEMINI_DEEP_MODEL"
        model = os.environ.get(env_var, default_model)
        return GeminiClient(model=model)
    elif provider == "claude":
        default_model = "claude-haiku-4-5-20251001" if role == "fast" else "claude-sonnet-4-6"
        env_var = "CLAUDE_FAST_MODEL" if role == "fast" else "CLAUDE_DEEP_MODEL"
        model = os.environ.get(env_var, default_model)
        return ClaudeClient(model=model)
    raise ValueError(f"Unknown PHOENIX_LLM provider: {provider}")



if __name__ == "__main__":
    # tiny smoke test: PHOENIX_LLM=gemini GEMINI_API_KEY=... python -m common.llm_client
    llm = get_llm("fast")
    print(f"provider model: {llm.model}")
    r = llm.complete(
        system="You are a terse assistant. Reply in 5 words or fewer.",
        messages=[{"role": "user", "content": "Say hello to Project Phoenix."}],
    )
    print("response:", r.text)
