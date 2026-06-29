# Contributing

Contributions are welcome, but keep the plugin small and API-only.

## Design constraints

- Do not mutate Hermes' system prompt.
- Do not persist `[time: ...]` prefixes into transcripts.
- Prefer `llm_request` middleware over core patches.
- Keep temporal reasoning in the model, not in plugin heuristics.
- Avoid provider-specific behavior unless it is covered by tests.

## Local checks

```bash
python -m pytest -q
python -m py_compile __init__.py time_awareness.py test_time_awareness.py
```

## Manual Hermes smoke test

After installing/enabling the plugin and starting a fresh Hermes session:

```bash
hermes chat -Q -t safe -q 'Prueba técnica: si ves un prefijo [time: ...], responde solo copiándolo; si no, responde NO_TIME.'
```

Expected output should start with:

```text
[time:
```

## When a Hermes core change is needed

If historical timestamps are unavailable to the middleware for a provider path, do not solve that by persisting prefixes. Propose a small generic Hermes core change that exposes original message timestamp metadata to `llm_request` middleware before provider-specific sanitization.
