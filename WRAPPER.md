# Stealth HTTP Wrapper & Multi-Tab / Context Forking Guide

`playwright-invisible-mcp` forks [`oss-invisible/playwright_mcp`](https://github.com/oss-invisible/playwright_mcp) and adds a production Streamable HTTP Docker wrapper under [`src/stealth_wrapper/`](src/stealth_wrapper/__init__.py) tailored for long-running containerized MCP gateways (such as MCG).

## Key Enhancements Over Upstream

- **Stateless Streamable HTTP + Health Probes**: Exposes `/mcp`, `/healthz`, and `/health` via Starlette/Uvicorn in [`server.py`](src/stealth_wrapper/server.py) with DNS rebinding protection disabled for internal Docker bridge networks.
- **Profile Lock & Session Resilience**: [`ProfileLifecycleGuard`](src/stealth_wrapper/profile_lifecycle_guard.py) clears stale Firefox symlink locks left by previous container IPs or dead PIDs, keeps session opens idempotent unless forced with `reopen=True`, and blocks auto-opening rogue sessions for unknown fork IDs.
- **Bounded SPA Stability Settling**: [`DomStabilitySettler`](src/stealth_wrapper/dom_stability_settler.py) translates `networkidle` to `load` and runs bounded DOM/resource settling after navigations and route-changing clicks.
- **Multi-Tab Pinned Routing (`tab_id`)**: [`TabRoutingService`](src/stealth_wrapper/tab_routing_service.py) preserves pinned active tabs and isolates concurrent async tool calls with `contextvars` overrides across page tools.
- **Fast Context Forking (`storage_state`)**: [`SessionForkService`](src/stealth_wrapper/session_fork_service.py) and [`ForkedStealthSession`](src/stealth_wrapper/forked_session.py) clone cookies and `localStorage` into isolated `BrowserContext` sessions for parallel or speculative browsing with promote/discard lifecycle controls.
- **Trusted Clipboard Paste, Bounded Reads & Authenticated Downloads**: [`BrowserActionService`](src/stealth_wrapper/browser_action_service.py) and [`PageDownloadService`](src/stealth_wrapper/page_download_service.py) add trusted `Control+v` clipboard pasting, scoped snapshots (`selector`, `viewport_only`, `max_elements`), `offset`/`max_chars` pagination, directional scrolling, `HttpOnly` cookie inspection, and authenticated browser downloads (including native CAD2 `expect_download` + Akamai comment block detection).

## Running via Docker (`ghcr.io/spelech/playwright-invisible-mcp`)

See [`.env.example`](.env.example), [`Dockerfile`](Dockerfile), and [`ARCHITECTURE.md`](ARCHITECTURE.md) for deployment topology and environment variables.
