# Contributing

Use Python 3.12 or newer. Keep changes platform-neutral, add tests for behavior,
and update requirement traceability when changing a contract.

```text
python -m pip install -e ".[dev]"
python -m ruff format --check .
python -m ruff check .
python -m mypy packages/allthecontext/src
python -m pytest
```

Never commit real personal context or credentials as fixtures.
