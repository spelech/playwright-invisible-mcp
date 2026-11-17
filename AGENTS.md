# AGENTS.md

Instructions and engineering guardrails for AI coding agents working on **`playwright-invisible-mcp`**.

## Guardrails & Rules
1. **Preserve Upstream Contract Surfaces**: Keep `src/invisible_playwright_mcp/` and upstream console script `[project.scripts]` (`invisible-playwright-mcp`) intact so upstream syncs stay conflict-free. Implement wrapper enhancements inside `src/stealth_wrapper/`.
2. **Modular File Size Cap (`≤ 500` LOC)**: Keep every source file, class, and test file `≤ 500` lines. Never use `*Manager`, `*Helper`, `*Util`, or `*Data` class names.
3. **Anti-Bot Stealth Invariants**: Enforce `isTrusted: true` clipboard/input events and never mutate the DOM directly via JavaScript. Keep `browser_evaluate` strictly read-only.
4. **Anti-Test-Theatre & Traceability**: Annotate every wrapper test in `tests/wrapper/` with `@pytest.mark.requirement("<CAT>-<NN>", ...)` synced 1:1 with `REQUIREMENTS.md`. Maintain `≥ 80%` coverage on `src/stealth_wrapper/` using real FastMCP/Starlette ASGI roundtrips and stateful fakes rather than mock verification assertions. Do not add `@pytest.mark.asyncio` (upstream `pyproject.toml` sets `asyncio_mode = "auto"` globally).
5. **Immutable Native Containers**: Target `linux/amd64` only in CI container builds and treat containers as immutable runtime units (`/healthz` and `/health` probes must respond HTTP 200).

## Verification Commands
```bash
# Release, manifest sync, link integrity, and anti-theatre audit
python3 scripts/verify_release.py --audit-tests --ci

# Linter & formatting
ruff check src/stealth_wrapper tests/wrapper

# Wrapper test suite with coverage (target ≥ 80%)
pytest tests/wrapper --cov=stealth_wrapper --cov-report=term-missing

# Upstream contract & unit tests (excluding network-gated release checks)
pytest --ignore=tests/test_release_pages.py --ignore=tests/test_version_is_not_taken.py
```
