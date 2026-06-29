"""Hermes Time Awareness plugin.

API-only middleware that prefixes user messages sent to the provider with
``[time: ISO-8601]`` metadata while leaving persisted transcripts clean.
"""

from __future__ import annotations

try:
    from .time_awareness import rewrite_llm_request
except ImportError:  # direct pytest/import-from-directory fallback
    from time_awareness import rewrite_llm_request


def register(ctx):
    """Register llm_request middleware."""
    ctx.register_middleware("llm_request", rewrite_llm_request)
