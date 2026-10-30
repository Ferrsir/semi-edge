# Testing

Install locked development dependencies with `uv sync --locked`. Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, and `uv run bandit -r src scripts -ll -q`. CI also builds a wheel, audits dependencies, scans secrets and lints workflows. Integration tests must use mocks or temporary local storage. No test receives broker credentials. Zero collected integration/safety tests fails CI. Governance test success is not proof of broker safety or merge enforcement.
