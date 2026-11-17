"""HTTP/Streamable-MCP server assembly and lifecycle bootstrap for stealth_wrapper."""

from __future__ import annotations

import atexit
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import uvicorn
from invisible_playwright_mcp.mcp import actions as _actions
from invisible_playwright_mcp.mcp import server as upstream_server
from starlette.responses import JSONResponse

from .browser_action_service import BrowserActionService
from .dom_stability_settler import DomStabilitySettler
from .page_download_service import PageDownloadService
from .profile_lifecycle_guard import ProfileLifecycleGuard
from .session_fork_service import SessionForkService
from .tool_adapter import register_wrapper_tools


@dataclass
class WrapperRuntime:
    """Assembled stealth wrapper services, FastMCP handlers, and Starlette ASGI application."""

    lifecycle_guard: ProfileLifecycleGuard
    dom_settler: DomStabilitySettler
    download_service: PageDownloadService
    action_service: BrowserActionService
    fork_service: SessionForkService
    tools: dict[str, Callable[..., Any]]
    app: Any
    healthz: Callable[..., Any]
    _mcp: Any
    _orig_tools: dict[str, Any]
    _orig_stateless_http: Any

    def restore(self) -> None:
        """Restore mutated upstream FastMCP tools and action/work hooks (used by test fixtures)."""
        self.fork_service.restore()
        self.action_service.restore()
        self.dom_settler.restore(_actions)
        self.lifecycle_guard.restore()
        self._mcp._tool_manager._tools.clear()
        self._mcp._tool_manager._tools.update(self._orig_tools)
        self._mcp.settings.stateless_http = self._orig_stateless_http


def build_wrapper_runtime(
    *,
    start_engine: bool = False,
    work_instance: Optional[Any] = None,
) -> WrapperRuntime:
    """Install wrapper domain services onto upstream FastMCP and return an ASGI app runtime."""
    mcp = upstream_server.mcp
    work = work_instance if work_instance is not None else upstream_server.work
    if start_engine:
        upstream_server.engine.start()

    default_profile_dir = os.environ.get("STEALTHFOX_PROFILE_DIR", "/data/profile")
    downloads_dir = Path(os.environ.get("STEALTHFOX_DOWNLOADS_DIR", "/data/downloads"))
    host_downloads_dir = os.environ.get("STEALTHFOX_HOST_DOWNLOADS_DIR") or str(downloads_dir)
    spa_settle_ms = int(os.environ.get("STEALTHFOX_SPA_SETTLE_MS", "2500"))
    spa_action_settle_ms = int(os.environ.get("STEALTHFOX_SPA_ACTION_SETTLE_MS", "2500"))

    orig_tools = dict(mcp._tool_manager._tools)
    orig_stateless_http = mcp.settings.stateless_http

    lifecycle_guard = ProfileLifecycleGuard(work, default_profile_dir=default_profile_dir)
    lifecycle_guard.install()

    dom_settler = DomStabilitySettler(
        navigation_settle_ms=spa_settle_ms,
        action_settle_ms=spa_action_settle_ms,
    )
    dom_settler.install(_actions)

    download_service = PageDownloadService(
        downloads_dir=downloads_dir,
        host_downloads_prefix=host_downloads_dir,
    )
    action_service = BrowserActionService(
        actions_module=_actions,
        downloads_dir=downloads_dir,
        host_downloads_prefix=host_downloads_dir,
        download_service=download_service,
    )
    action_service.install()

    fork_service = SessionForkService(work, actions_module=_actions)
    fork_service.install()

    tools = register_wrapper_tools(
        mcp=mcp,
        work=work,
        actions=_actions,
        action_service=action_service,
        fork_service=fork_service,
    )

    if hasattr(mcp.settings, "transport_security") and mcp.settings.transport_security:
        mcp.settings.transport_security.enable_dns_rebinding_protection = False
        mcp.settings.transport_security.allowed_hosts = ["*"]
        mcp.settings.transport_security.allowed_origins = ["*"]

    mcp.settings.stateless_http = True
    app = mcp.streamable_http_app()

    async def healthz(_request: Any) -> JSONResponse:
        return JSONResponse(
            {
                "status": "healthy",
                "service": "invisible-playwright-mcp",
                "version": getattr(upstream_server, "__version__", "0.73.1"),
                "headless": os.environ.get("STEALTHFOX_HEADLESS", "1"),
            }
        )

    app.add_route("/healthz", healthz)
    app.add_route("/health", healthz)

    return WrapperRuntime(
        lifecycle_guard=lifecycle_guard,
        dom_settler=dom_settler,
        download_service=download_service,
        action_service=action_service,
        fork_service=fork_service,
        tools=tools,
        app=app,
        healthz=healthz,
        _mcp=mcp,
        _orig_tools=orig_tools,
        _orig_stateless_http=orig_stateless_http,
    )


def main() -> None:
    """Start the stealth Playwright MCP server over Streamable HTTP or stdio."""
    atexit.register(upstream_server._close_sessions_at_exit)
    runtime = build_wrapper_runtime(start_engine=True)
    transport = os.environ.get("STEALTHFOX_MCP_TRANSPORT", "http").strip().lower()
    if transport == "stdio":
        upstream_server.mcp.run(transport="stdio")
        return
    host = os.environ.get("STEALTHFOX_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("STEALTHFOX_MCP_PORT", "8000"))
    uvicorn.run(runtime.app, host=host, port=port, log_level="info")
