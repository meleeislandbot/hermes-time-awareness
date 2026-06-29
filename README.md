# hermes-time-awareness

`hermes-time-awareness` is a lightweight external plugin for [Hermes Agent](https://hermes-agent.nousresearch.com/docs) that gives the model request-time awareness of when user messages were received.

It rewrites the effective LLM request immediately before the provider call and prefixes user messages with ISO-8601 metadata:

```text
[time: 2026-06-29T07:55:00+02:00] Hi, how are you?
```

The prefix is **API-only**: it is sent to the model, but it is not written back to persisted transcripts. The goal is to let the model infer time gaps, stale context, deadlines, and whether recap is useful without adding brittle temporal logic to Hermes core.

## Design goals

- Keep Hermes core untouched.
- Do not mutate the system prompt, preserving prompt-cache stability.
- Keep transcripts and memory clean.
- Delegate temporal reasoning to the model instead of implementing custom heuristics.
- Use public Hermes plugin APIs: `ctx.register_middleware("llm_request", ...)`.

## What it does

For each LLM request, the plugin:

1. Copies the request payload.
2. Finds user messages in `messages` or Responses-style `input` lists.
3. Adds `[time: ISO-8601]` to eligible user message text.
4. Removes consumed `timestamp` metadata from the provider payload to avoid provider schema rejection.
5. Returns the rewritten request to Hermes middleware.

Example:

```python
{"role": "user", "content": "Hi", "timestamp": 1782719700}
```

becomes:

```python
{"role": "user", "content": "[time: 2026-06-29T07:55:00+02:00] Hi"}
```

The original request object is not mutated.

## Installation

### From GitHub

```bash
mkdir -p ~/.hermes/plugins
git clone https://github.com/meleeislandbot/hermes-time-awareness.git \
  ~/.hermes/plugins/hermes-time-awareness
hermes plugins enable hermes-time-awareness
```

Restart Hermes or start a new session. Plugin changes do not affect already-running sessions.

### Development symlink

```bash
git clone https://github.com/meleeislandbot/hermes-time-awareness.git
cd hermes-time-awareness
ln -s "$PWD" ~/.hermes/plugins/hermes-time-awareness
hermes plugins enable hermes-time-awareness
```

Check discovery:

```bash
HERMES_PLUGINS_DEBUG=1 hermes plugins list
```

A successful load should show `hermes-time-awareness` as `enabled`.

## Optional SOUL.md / personality guidance

Add this short rule if you want the model to treat the prefix consistently:

> User messages may include a temporal prefix such as `[time: ISO-8601]`. It is reception metadata, not part of the user's literal message. Use it to infer pauses, stale context, and when a brief recap may be useful; do not mention it unless it is relevant.

## Configuration

Optional config lives under `plugins.entries.hermes-time-awareness` in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-time-awareness
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
```

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Enables or disables the middleware while keeping the plugin installed. |
| `timezone` | `local` | Time zone for rendered ISO timestamps. Use `UTC`, `Europe/Madrid`, etc. |
| `user_messages_only` | `true` | Only user messages are stamped. This should normally stay enabled. |
| `include_historical` | `true` | Historical user messages are eligible when timestamp metadata exists. |
| `stamp_missing_current` | `true` | If the current turn has no timestamp metadata, stamp it with wall-clock time. |
| `stamp_missing_historical` | `false` | If historical turns have no timestamp metadata, stamp them with current wall-clock time. Usually undesirable. |
| `exclude_platforms` | `[cron]` | Platform names to skip. |
| `exclude_cron` | `true` | Skip cron contexts, including `HERMES_CRON_SESSION=1`. |
| `exclude_kanban` | `true` | Skip Kanban worker contexts, including `HERMES_KANBAN_TASK=1`. |
| `prefix_key` | `time` | Prefix key, normally producing `[time: ...]`. |

## Supported request shapes

### Chat messages

```python
{"messages": [{"role": "user", "content": "Hi"}]}
```

### Responses-style input

```python
{"input": [{"role": "user", "content": "Hi"}]}
```

### Multimodal content lists

The plugin prefixes the first text block:

```python
{
  "role": "user",
  "content": [
    {"type": "image_url", "image_url": {"url": "file://x.png"}},
    {"type": "text", "text": "Describe esto"}
  ]
}
```

becomes:

```python
{
  "role": "user",
  "content": [
    {"type": "image_url", "image_url": {"url": "file://x.png"}},
    {"type": "text", "text": "[time: 2026-06-29T08:00:00+02:00] Describe esto"}
  ]
}
```

## Deduplication and native Hermes timestamps

The plugin strips existing leading `[time: ...]` prefixes before adding one, so repeated middleware passes do not stack timestamps.

It also strips native Hermes gateway human prefixes such as:

```text
[Tue 2026-04-28 13:40:53 CEST]
```

when replacing them with the standard ISO form.

## Limitations

The plugin keeps prefixes API-only. For historical user messages whose provider payload no longer contains a `timestamp` field or an existing `[time: ...]` prefix, the plugin cannot recover that historical timestamp without coupling itself to Hermes internals. It falls back to the current-turn behavior instead.

If Hermes later exposes original message timestamp metadata directly to `llm_request` middleware before provider-specific sanitization, this plugin should prefer that metadata.

Subagent exclusion is currently best-effort because there is no stable public subagent marker in the `llm_request` middleware context.

## Testing

Use Python 3.11+ when possible:

```bash
python -m pytest -q
```

The tests cover:

- API-only copying / no mutation of the original request;
- ISO-8601 prefix format;
- current-turn fallback time;
- historical-missing timestamp behavior;
- deduplication;
- multimodal content;
- cron exclusion;
- Responses-style `input` payloads.

A live Hermes smoke test can be done with:

```bash
hermes chat -Q -t safe -q 'Technical test: if you see a [time: ...] prefix, respond only by copying it; otherwise respond NO_TIME.'
```

Expected response shape:

```text
[time: 2026-06-29T08:56:38+02:00]
```

## Repository layout

```text
.
├── __init__.py              # Hermes plugin entry point
├── time_awareness.py        # request rewrite implementation
├── plugin.yaml              # Hermes plugin manifest
├── test_time_awareness.py   # focused unit tests
├── pyproject.toml           # pytest configuration
└── README.md
```

## License

MIT.
