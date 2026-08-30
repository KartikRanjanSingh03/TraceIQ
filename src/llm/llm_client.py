"""
src/llm/llm_client.py — Provider-agnostic LLM client for TraceIQ.

Per 09_LLM_ARCHITECTURE.md §8: model is swappable behind a single interface.
Switching providers requires no changes to prompts, evidence contract, or downstream code.

Supported providers (selected via LLM_PROVIDER env var):
  mock    — deterministic, no API call (default for tests and offline runs)
  gemini  — Google Gemini API (free-tier capable)
  openai  — OpenAI API

Per CLAUDE.md §4: provider and model are config-driven, not hardcoded.
All LLM calls are logged for telemetry (Stage 14).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


# ── Response type ──────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Raw response from one LLM call."""
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.text)


# ── Simple in-memory cache (per 09_LLM_ARCHITECTURE §9) ───────────────────

_CACHE: dict[str, LLMResponse] = {}


def _cache_key(prompt: str, system: str, model: str) -> str:
    raw = f"{model}||{system}||{prompt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Provider implementations ───────────────────────────────────────────────

def _call_mock(prompt: str, system: str, model: str = "mock") -> LLMResponse:
    """
    Mock provider: returns a structured but deterministic narrative.
    Used in all tests — no API key required, no network call.
    The mock extracts key facts from the evidence package JSON embedded in the prompt
    and produces a minimal but structurally correct narrative for test assertions.
    """
    # Extract a few facts from the prompt for a realistic-looking response
    has_stockout   = "stockout" in prompt.lower() or "inventory" in prompt.lower()
    has_competitor = "competitor" in prompt.lower()
    is_analyst     = "analyst" in system.lower() or "full evidence" in system.lower()
    is_abstention  = "insufficient evidence" in prompt.lower()

    if is_abstention:
        text = (
            "Based on the evidence provided, the system cannot determine the root cause "
            "with sufficient confidence. The evidence is insufficient or contradictory. "
            "No recommendation has been issued. Please collect the missing data listed "
            "before taking action."
        )
    elif has_stockout and is_analyst:
        text = (
            "Analysis: North/Electronics Revenue declined due to an inventory shortage. "
            "Stockout rate reached ~100% (baseline <1%), confirmed by ops report "
            "PLANTED-OUTAGE-001 (similarity 0.72). "
            "Confidence: Strong (score 0.75). "
            "Dominant factor: Orders (−65% vs baseline, attributable to zero stock). "
            "Recommendation: Expedite replenishment — Supply Chain Manager to action within 24h."
        )
    elif has_stockout:
        text = (
            "Revenue in North Electronics dropped this week due to a warehouse stockout. "
            "Stock levels fell to zero, cutting customer orders significantly. "
            "Action required: Supply Chain team should prioritise restocking immediately."
        )
    elif has_competitor:
        text = (
            "A competitor promotion was mentioned in market intelligence notes, "
            "but evidence is insufficient to confirm this as the primary cause. "
            "No recommendation has been issued pending further investigation."
        )
    else:
        text = "Narrative generated from provided evidence package."

    return LLMResponse(
        text=text,
        provider="mock",
        model=model,
        prompt_tokens=len(prompt.split()),
        completion_tokens=len(text.split()),
        latency_ms=1.0,
        cached=False,
    )


def _call_gemini(prompt: str, system: str, model: str) -> LLMResponse:
    """Gemini API provider (requires GEMINI_API_KEY env var)."""
    try:
        import google.generativeai as genai  # type: ignore
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        t0  = time.time()
        mdl = genai.GenerativeModel(
            model_name=model,
            system_instruction=system,
        )
        resp = mdl.generate_content(prompt)
        latency = (time.time() - t0) * 1000
        return LLMResponse(
            text=resp.text,
            provider="gemini",
            model=model,
            prompt_tokens=resp.usage_metadata.prompt_token_count if hasattr(resp, "usage_metadata") else 0,
            completion_tokens=resp.usage_metadata.candidates_token_count if hasattr(resp, "usage_metadata") else 0,
            latency_ms=latency,
        )
    except Exception as e:
        return LLMResponse(text="", provider="gemini", model=model, error=str(e))


def _call_openai(prompt: str, system: str, model: str) -> LLMResponse:
    """OpenAI API provider (requires OPENAI_API_KEY env var)."""
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system",    "content": system},
                {"role": "user",      "content": prompt},
            ],
            max_tokens=512,
        )
        latency = (time.time() - t0) * 1000
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            provider="openai",
            model=model,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            latency_ms=latency,
        )
    except Exception as e:
        return LLMResponse(text="", provider="openai", model=model, error=str(e))


# ── Dispatcher ────────────────────────────────────────────────────────────

_PROVIDERS = {
    "mock":   _call_mock,
    "gemini": _call_gemini,
    "openai": _call_openai,
}

_DEFAULT_MODELS = {
    "mock":   "mock-v1",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
}


def call(
    prompt: str,
    system: str = "",
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    use_cache: bool = True,
) -> LLMResponse:
    """
    Single entry point for all LLM calls in TraceIQ.

    Args:
        prompt:     User/content prompt (contains the evidence package)
        system:     System instruction (persona + behavioral rules)
        provider:   Override provider. Falls back to LLM_PROVIDER env var, then "mock".
        model:      Override model. Falls back to provider-specific default.
        use_cache:  Cache identical (prompt, system, model) calls (per §9)

    Returns:
        LLMResponse
    """
    _provider = provider or os.environ.get("LLM_PROVIDER", "mock")
    _model    = model or os.environ.get("LLM_MODEL", _DEFAULT_MODELS.get(_provider, "mock-v1"))

    if use_cache:
        key = _cache_key(prompt, system, _model)
        if key in _CACHE:
            cached = _CACHE[key]
            return LLMResponse(
                text=cached.text,
                provider=cached.provider,
                model=cached.model,
                prompt_tokens=cached.prompt_tokens,
                completion_tokens=cached.completion_tokens,
                latency_ms=0.0,
                cached=True,
            )

    fn = _PROVIDERS.get(_provider, _call_mock)
    resp = fn(prompt, system, _model)

    if use_cache and resp.success:
        key = _cache_key(prompt, system, _model)
        _CACHE[key] = resp

    return resp
