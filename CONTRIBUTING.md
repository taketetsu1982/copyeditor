# Contributing

copyeditor is a small Japanese text rewriting MCP server. Keep changes focused on the single `polish_text` contract in [contracts/polish-text.md](contracts/polish-text.md).

Use Python 3.12. Install `requirements.generated.txt`, then run `python -m pytest tests -q`. CI uses fake model responses and local OAuth fixtures; it needs no cloud credentials. Production dependencies are declared in `pyproject.toml`. Regenerate the lock with `pip-compile --extra=test --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host -o requirements.generated.txt pyproject.toml`.

Prompt changes require a separate, explicitly authorized evaluation with externally supplied data. Never commit private evaluation inputs or outputs. Do not add document text, credentials, or real project identifiers to issues, tests, or logs. Use synthetic fixtures.

Public code, tests, documentation, commits, and PR descriptions use English. The Japanese prompt and Japanese examples are exceptions. Explain behavior and validation in the PR; flag any unverified behavior. Publishing and deployment are separate owner decisions.
