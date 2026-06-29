"""Request rewriting helpers for hermes-time-awareness."""

from __future__ import annotations

import os
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

PLUGIN_ID = "hermes-time-awareness"

_TIME_PREFIX_RE = re.compile(
    r"^\[time:\s*(?P<iso>\d{4}-\d{2}-\d{2}T[^\]]+)\]\s*",
    re.IGNORECASE,
)

# Native gateway format as of Hermes 2026: [Tue 2026-04-28 13:40:53 CEST]
_GATEWAY_HUMAN_PREFIX_RE = re.compile(
    r"^\[(?P<dow>[A-Z][a-z]{2})\s+"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\s+(?P<tz>[A-Za-z0-9_+\-/:]+))?\]\s*"
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class TimeAwarenessConfig:
    enabled: bool = True
    timezone_name: str = "local"
    user_messages_only: bool = True
    include_historical: bool = True
    stamp_missing_current: bool = True
    stamp_missing_historical: bool = False
    exclude_platforms: tuple[str, ...] = ("cron",)
    exclude_cron: bool = True
    exclude_kanban: bool = True
    exclude_subagents: bool = False  # best-effort placeholder; see README
    prefix_key: str = "time"
    source: str = PLUGIN_ID


def rewrite_llm_request(*, request: dict[str, Any], **context: Any) -> Optional[dict[str, Any]]:
    """Rewrite the effective provider request with temporal metadata.

    Contract for Hermes ``llm_request`` middleware: return ``{"request": new_request}``
    to replace the payload, or ``None`` to leave it unchanged.
    """
    cfg = load_config()
    if not cfg.enabled or _is_excluded_context(cfg, context):
        return None

    new_request = deepcopy(request)
    changed = False
    now_epoch = time.time()

    for key in ("messages", "input"):
        value = new_request.get(key)
        if isinstance(value, list):
            did_change = _rewrite_message_list(
                value,
                cfg=cfg,
                now_epoch=now_epoch,
            )
            changed = changed or did_change

    if not changed:
        return None
    return {"request": new_request, "source": cfg.source, "reason": "time-awareness"}


def load_config() -> TimeAwarenessConfig:
    """Load optional plugin config from Hermes config.yaml.

    Supported config path:

    plugins:
      entries:
        hermes-time-awareness:
          enabled: true
          timezone: Europe/Madrid
          user_messages_only: true
          include_historical: true
          stamp_missing_current: true
          stamp_missing_historical: false
          exclude_platforms: [cron]
          exclude_cron: true
          exclude_kanban: true
    """
    data: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config as _load_config

        root = _load_config() or {}
        plugins = root.get("plugins") or {}
        entries = plugins.get("entries") or {}
        if isinstance(entries, dict):
            data = entries.get(PLUGIN_ID) or entries.get("time-awareness") or {}
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    return TimeAwarenessConfig(
        enabled=_bool(data.get("enabled"), True),
        timezone_name=str(data.get("timezone") or data.get("tz") or "local"),
        user_messages_only=_bool(data.get("user_messages_only"), True),
        include_historical=_bool(data.get("include_historical"), True),
        stamp_missing_current=_bool(data.get("stamp_missing_current"), True),
        stamp_missing_historical=_bool(data.get("stamp_missing_historical"), False),
        exclude_platforms=tuple(
            str(x).strip().lower()
            for x in _list(data.get("exclude_platforms"), default=["cron"])
            if str(x).strip()
        ),
        exclude_cron=_bool(data.get("exclude_cron"), True),
        exclude_kanban=_bool(data.get("exclude_kanban"), True),
        exclude_subagents=_bool(data.get("exclude_subagents"), False),
        prefix_key=str(data.get("prefix_key") or "time"),
        source=str(data.get("source") or PLUGIN_ID),
    )


def _rewrite_message_list(
    messages: list[Any], *, cfg: TimeAwarenessConfig, now_epoch: float
) -> bool:
    user_indexes = [
        idx for idx, msg in enumerate(messages)
        if isinstance(msg, dict) and msg.get("role") == "user"
    ]
    last_user_idx = user_indexes[-1] if user_indexes else None
    changed = False

    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if cfg.user_messages_only and role != "user":
            continue
        if role != "user":
            continue
        if idx != last_user_idx and not cfg.include_historical:
            continue

        content = msg.get("content")
        clean_content, embedded_epoch = _strip_known_time_prefixes(content)
        if clean_content is not content:
            msg["content"] = clean_content
            content = clean_content
            changed = True

        explicit_ts = msg.get("timestamp")
        epoch = _coerce_epoch(explicit_ts, cfg=cfg)
        if epoch is None:
            epoch = embedded_epoch
        if epoch is None:
            if idx == last_user_idx and cfg.stamp_missing_current:
                epoch = now_epoch
            elif idx != last_user_idx and cfg.stamp_missing_historical:
                epoch = now_epoch

        if epoch is None:
            continue

        if _prefix_content(msg, epoch, cfg=cfg):
            # Remove provider-rejected metadata after we consumed it.
            msg.pop("timestamp", None)
            changed = True

    return changed


def _prefix_content(msg: dict[str, Any], epoch: float, *, cfg: TimeAwarenessConfig) -> bool:
    prefix = _format_prefix(epoch, cfg=cfg)
    content = msg.get("content")

    if isinstance(content, str):
        clean, _ = _strip_known_time_prefixes(content)
        msg["content"] = f"{prefix} {clean}" if clean else prefix
        return True

    if isinstance(content, list):
        # OpenAI/Responses multimodal shapes: prefix the first text block.
        for part in content:
            if not isinstance(part, dict):
                continue
            text_key = None
            if isinstance(part.get("text"), str):
                text_key = "text"
            elif isinstance(part.get("content"), str):
                text_key = "content"
            if text_key:
                clean, _ = _strip_known_time_prefixes(part[text_key])
                part[text_key] = f"{prefix} {clean}" if clean else prefix
                return True
        content.insert(0, {"type": "text", "text": prefix})
        return True

    return False


def _format_prefix(epoch: float, *, cfg: TimeAwarenessConfig) -> str:
    dt = datetime.fromtimestamp(epoch, tz=_timezone(cfg.timezone_name))
    return f"[{cfg.prefix_key}: {dt.isoformat(timespec='seconds')}]"


def _timezone(name: str):
    raw = (name or "local").strip()
    if not raw or raw.lower() == "local":
        return datetime.now().astimezone().tzinfo
    if raw.upper() == "UTC":
        return timezone.utc
    if ZoneInfo is None:
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(raw)
    except Exception:
        return datetime.now().astimezone().tzinfo


def _strip_known_time_prefixes(content: Any) -> tuple[Any, Optional[float]]:
    if isinstance(content, str):
        clean, epoch = _strip_string_prefixes(content)
        return clean, epoch
    if isinstance(content, list):
        embedded_epoch = None
        changed = False
        new_parts = []
        for part in content:
            if isinstance(part, dict):
                copied = dict(part)
                for key in ("text", "content"):
                    value = copied.get(key)
                    if isinstance(value, str):
                        clean, epoch = _strip_string_prefixes(value)
                        if clean != value:
                            copied[key] = clean
                            changed = True
                            if epoch is not None:
                                embedded_epoch = epoch
                            break
                new_parts.append(copied)
            else:
                new_parts.append(part)
        return (new_parts if changed else content), embedded_epoch
    return content, None


def _strip_string_prefixes(text: str) -> tuple[str, Optional[float]]:
    embedded_epoch = None
    out = text
    while True:
        match = _TIME_PREFIX_RE.match(out)
        if match:
            embedded_epoch = _coerce_iso(match.group("iso")) or embedded_epoch
            out = out[match.end():]
            continue
        human = _GATEWAY_HUMAN_PREFIX_RE.match(out)
        if human:
            # Strip native gateway timestamp if present so this plugin can
            # standardize on [time: ISO-8601] without duplicate prefixes.
            out = out[human.end():]
            continue
        break
    return out, embedded_epoch


def _coerce_epoch(value: Any, *, cfg: TimeAwarenessConfig) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except Exception:
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            pass
        dt = _parse_datetime(stripped, cfg=cfg)
        if dt is not None:
            return float(dt.timestamp())
    return None


def _coerce_iso(value: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(value.strip())
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return float(dt.timestamp())


def _parse_datetime(value: str, *, cfg: TimeAwarenessConfig) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_timezone(cfg.timezone_name))
    return dt


def _is_excluded_context(cfg: TimeAwarenessConfig, context: dict[str, Any]) -> bool:
    platform = str(context.get("platform") or "").strip().lower()
    if platform and platform in cfg.exclude_platforms:
        return True
    if cfg.exclude_cron and (platform == "cron" or _env_truthy("HERMES_CRON_SESSION")):
        return True
    if cfg.exclude_kanban and _env_truthy("HERMES_KANBAN_TASK"):
        return True
    # There is no stable public subagent marker in llm_request context today.
    # Keep this opt-in flag for future core support; fail open for now.
    return False


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE


def _list(value: Any, *, default: Iterable[Any]) -> list[Any]:
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]
