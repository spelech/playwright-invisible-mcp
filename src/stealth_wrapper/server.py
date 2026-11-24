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
from .credential_access_service import (
    DEFAULT_AAC_PROXY_URL,
    DEFAULT_AAC_TIMEOUT_S,
    CredentialAccessService,
)
from .dom_stability_settler import DomStabilitySettler
from .mcp_prompts import register_wrapper_prompts
from .page_download_service import PageDownloadService
from .profile_lifecycle_guard import ProfileLifecycleGuard
from .session_fork_service import SessionForkService
from .tool_adapter import register_wrapper_tools
from .tool_trace_recorder import ToolTraceRecorder


@dataclass
class WrapperRuntime:
    """Assembled stealth wrapper services, FastMCP handlers, and Starlette ASGI application."""

    lifecycle_guard: ProfileLifecycleGuard
    dom_settler: DomStabilitySettler
    download_service: PageDownloadService
    action_service: BrowserActionService
    fork_service: SessionForkService
    trace_recorder: ToolTraceRecorder
    credential_service: CredentialAccessService
    tools: dict[str, Callable[..., Any]]
    app: Any
    healthz: Callable[..., Any]
    traces: Callable[..., Any]
    _mcp: Any
    _orig_tools: dict[str, Any]
    _orig_stateless_http: Any
    _orig_instructions: Any
    _orig_prompts: dict[str, Any]
    _orig_resources: dict[str, Any]

    def restore(self) -> None:
        """Restore mutated upstream FastMCP tools, prompts, resources, and action/work hooks."""
        self.fork_service.restore()
        self.action_service.restore()
        self.dom_settler.restore(_actions)
        self.lifecycle_guard.restore()
        self._mcp._tool_manager._tools.clear()
        self._mcp._tool_manager._tools.update(self._orig_tools)
        if hasattr(self._mcp, "_prompt_manager"):
            self._mcp._prompt_manager._prompts.clear()
            self._mcp._prompt_manager._prompts.update(self._orig_prompts)
        if hasattr(self._mcp, "_resource_manager"):
            self._mcp._resource_manager._resources.clear()
            self._mcp._resource_manager._resources.update(self._orig_resources)
        self._mcp._mcp_server.instructions = self._orig_instructions
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
    trace_enabled = os.environ.get("STEALTHFOX_TRACE_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    trace_file_raw = os.environ.get("STEALTHFOX_TRACE_FILE", "/data/traces/tool_calls.jsonl").strip()
    trace_file = Path(trace_file_raw) if trace_file_raw else None
    trace_max_events = int(os.environ.get("STEALTHFOX_TRACE_MAX_EVENTS", "500"))
    trace_log = os.environ.get("STEALTHFOX_TRACE_LOG", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    aac_enabled = os.environ.get("STEALTHFOX_AAC_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    aac_bin = os.environ.get("STEALTHFOX_AAC_BIN", "aac").strip() or "aac"
    aac_proxy_url = os.environ.get("STEALTHFOX_AAC_PROXY_URL", DEFAULT_AAC_PROXY_URL).strip()
    aac_home_raw = os.environ.get("STEALTHFOX_AAC_HOME", "/data/aac").strip()
    aac_home = Path(aac_home_raw) if aac_home_raw else None
    aac_session = os.environ.get("STEALTHFOX_AAC_SESSION")
    aac_token = os.environ.get("STEALTHFOX_AAC_TOKEN")
    aac_timeout_s = int(os.environ.get("STEALTHFOX_AAC_TIMEOUT_S", str(DEFAULT_AAC_TIMEOUT_S)))

    orig_tools = {
        name: tool_obj.model_copy(deep=True) if hasattr(tool_obj, "model_copy") else tool_obj
        for name, tool_obj in mcp._tool_manager._tools.items()
    }
    orig_prompts = dict(getattr(getattr(mcp, "_prompt_manager", None), "_prompts", {}))
    orig_resources = dict(getattr(getattr(mcp, "_resource_manager", None), "_resources", {}))
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

    trace_recorder = ToolTraceRecorder(
        work,
        enabled=trace_enabled,
        trace_file=trace_file,
        max_events=trace_max_events,
        log_events=trace_log,
    )

    credential_service = CredentialAccessService(
        actions_module=_actions,
        enabled=aac_enabled,
        aac_bin=aac_bin,
        proxy_url=aac_proxy_url,
        aac_home=aac_home,
        session_id=aac_session,
        bootstrap_token=aac_token,
        timeout_seconds=aac_timeout_s,
    )

    prompt_state = register_wrapper_prompts(mcp)

    tools = register_wrapper_tools(
        mcp=mcp,
        work=work,
        actions=_actions,
        action_service=action_service,
        fork_service=fork_service,
        trace_recorder=trace_recorder,
        credential_service=credential_service,
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
                "traceEnabled": trace_recorder.enabled,
                "tracedCalls": trace_recorder._total_calls,
                "aacEnabled": credential_service.enabled,
                "aacAvailable": bool(credential_service.resolve_binary()),
            }
        )

    async def traces(request: Any) -> JSONResponse:
        params = getattr(request, "query_params", {}) or {}
        limit_recent = int(params.get("limit", "25"))
        min_count = int(params.get("min_count", "2"))
        reset = str(params.get("reset", "0")).strip().lower() in ("1", "true", "yes")
        summary = trace_recorder.summarize(
            limit_recent=limit_recent,
            min_sequence_count=min_count,
            reset=reset,
        )
        return JSONResponse(summary.model_dump())

    app.add_route("/healthz", healthz)
    app.add_route("/health", healthz)
    app.add_route("/traces", traces)

    return WrapperRuntime(
        lifecycle_guard=lifecycle_guard,
        dom_settler=dom_settler,
        download_service=download_service,
        action_service=action_service,
        fork_service=fork_service,
        trace_recorder=trace_recorder,
        credential_service=credential_service,
        tools=tools,
        app=app,
        healthz=healthz,
        traces=traces,
        _mcp=mcp,
        _orig_tools=orig_tools,
        _orig_stateless_http=orig_stateless_http,
        _orig_instructions=prompt_state["orig_instructions"],
        _orig_prompts=orig_prompts,
        _orig_resources=orig_resources,
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
