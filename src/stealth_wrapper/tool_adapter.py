"""FastMCP tool schema adapter and enhanced wrapper tool registration."""

from __future__ import annotations

import inspect
from typing import Annotated, Any, Callable, Optional, get_type_hints

from invisible_playwright_mcp.mcp.server import DEFAULT_BROWSER_ID, _says
from pydantic import Field

from .models import DEFAULT_READ_HTML_MAX_CHARS, DEFAULT_READ_TEXT_MAX_CHARS

FlexibleBrowser = Annotated[
    Optional[str],
    Field(
        description="Browser session ID (`main`, `support`, or a forked session ID). Defaults to `main`."
    ),
]
TabIdParam = Annotated[
    Optional[int],
    Field(
        description="Optional 0-based tab ID within the browser session for this call only (keeps the active tab pinned)."
    ),
]

SESSION_ADAPTED_TOOLS = ("browser_close", "browser_status")
PAGE_ADAPTED_TOOLS = (
    "browser_take_screenshot",
    "browser_watch",
    "browser_click",
    "browser_click_at",
    "browser_type",
    "browser_select_option",
    "browser_upload_files",
    "browser_press_key",
)
REPLACED_TOOLS = (
    "browser_open",
    "browser_navigate",
    "browser_read_text",
    "browser_snapshot",
    "browser_read_html",
    "browser_evaluate",
)


def adapt_upstream_tool(
    mcp: Any,
    tab_scope_fn: Callable[[Optional[int]], Any],
    tool_name: str,
    *,
    add_tab_id: bool,
) -> None:
    """Relax `browser` Literal constraints to accept fork IDs and optionally inject `tab_id` routing."""
    orig_tool = mcp._tool_manager._tools.get(tool_name)
    if orig_tool is None:
        return
    orig_fn = orig_tool.fn
    sig = inspect.signature(orig_fn)
    try:
        hints = get_type_hints(orig_fn, include_extras=True)
    except Exception:
        hints = dict(getattr(orig_fn, "__annotations__", {}))

    new_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    orig_param_names = list(sig.parameters.keys())

    for name, param in sig.parameters.items():
        if name == "browser":
            new_params.append(param.replace(annotation=FlexibleBrowser, default=None))
            annotations["browser"] = FlexibleBrowser
        else:
            new_params.append(param)
            if name in hints:
                annotations[name] = hints[name]

    if add_tab_id and "tab_id" not in sig.parameters:
        new_params.append(
            inspect.Parameter(
                "tab_id",
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
                annotation=TabIdParam,
            )
        )
        annotations["tab_id"] = TabIdParam

    if "return" in hints:
        annotations["return"] = hints["return"]

    all_param_names = [p.name for p in new_params]

    async def _adapted(*args: Any, **kwargs: Any) -> Any:
        bound = dict(zip(all_param_names, args))
        bound.update(kwargs)
        tab_id = bound.pop("tab_id", None) if add_tab_id else None
        call_kwargs = {k: bound[k] for k in orig_param_names if k in bound}
        with tab_scope_fn(tab_id):
            return await orig_fn(**call_kwargs)

    _adapted.__name__ = orig_fn.__name__
    _adapted.__doc__ = orig_tool.description or orig_fn.__doc__
    _adapted.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
    _adapted.__annotations__ = annotations

    mcp._tool_manager.remove_tool(tool_name)
    mcp.add_tool(
        _adapted,
        name=tool_name,
        description=orig_tool.description,
        annotations=orig_tool.annotations,
        structured_output=orig_tool.fn_metadata.output_schema is not None,
    )


def register_wrapper_tools(
    *,
    mcp: Any,
    work: Any,
    actions: Any,
    action_service: Any,
    fork_service: Any,
    trace_recorder: Optional[Any] = None,
) -> dict[str, Callable[..., Any]]:
    """Adapt upstream FastMCP tools and register all wrapper-enhanced and new browser tools."""
    for session_tool in SESSION_ADAPTED_TOOLS:
        adapt_upstream_tool(mcp, fork_service.tab_scope, session_tool, add_tab_id=False)

    for page_tool in PAGE_ADAPTED_TOOLS:
        adapt_upstream_tool(mcp, fork_service.tab_scope, page_tool, add_tab_id=True)

    for tool_to_replace in REPLACED_TOOLS:
        try:
            mcp._tool_manager.remove_tool(tool_to_replace)
        except Exception:
            pass

    @mcp.tool(annotations=_says("Open a browser", destructive=True, open_world=False))
    async def browser_open(
        browser: FlexibleBrowser = None,
        seed: int | None = None,
        proxy: str | None = None,
        profile: str | None = None,
        reopen: bool = False,
    ) -> str:
        """Open `main` or `support`, or verify a browser session is open and ready."""
        return await work.open(
            browser or DEFAULT_BROWSER_ID,
            seed=seed,
            proxy=proxy,
            profile=profile,
            reopen=reopen,
        )

    @mcp.tool(annotations=_says("Go to a URL", destructive=True))
    async def browser_navigate(
        url: str,
        wait_until: str = "domcontentloaded",
        waitUntil: Optional[str] = None,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Go to a url in this browser's page, accepting `wait_until` or `waitUntil` alias."""
        effective_wait = waitUntil or wait_until or "domcontentloaded"
        with fork_service.tab_scope(tab_id):
            return await work.acting(actions.navigate, url, wait_until=effective_wait, role=browser)

    @mcp.tool(annotations=_says("Read visible page text", read_only=True))
    async def browser_read_text(
        selector: str = "body",
        max_chars: int = DEFAULT_READ_TEXT_MAX_CHARS,
        offset: int = 0,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Read visible text inside `selector`, capped by `max_chars` with `offset` pagination."""
        with fork_service.tab_scope(tab_id):
            return await work.reading(
                action_service.read_text_bounded,
                selector=selector,
                max_chars=max_chars,
                offset=offset,
                role=browser,
            )

    @mcp.tool(annotations=_says("Snapshot the page", read_only=True))
    async def browser_snapshot(
        selector: Optional[str] = None,
        viewport_only: bool = False,
        max_elements: int = 0,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Title, url, and visible interactive elements with optional selector/viewport/count caps."""
        with fork_service.tab_scope(tab_id):
            return await work.reading(
                actions.snapshot,
                selector=selector,
                viewport_only=viewport_only,
                max_elements=max_elements,
                role=browser,
            )

    @mcp.tool(annotations=_says("Read cleaned page HTML", read_only=True))
    async def browser_read_html(
        mode: str = "form",
        max_chars: int = DEFAULT_READ_HTML_MAX_CHARS,
        offset: int = 0,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Read cleaned page HTML (`form`, `text`, or `full`), capped by `max_chars` with `offset`."""
        with fork_service.tab_scope(tab_id):
            return await work.reading(
                action_service.read_html_bounded,
                mode=mode,
                max_chars=max_chars,
                offset=offset,
                role=browser,
            )

    @mcp.tool(annotations=_says("Read the page with JavaScript", read_only=True))
    async def browser_evaluate(
        expression: Optional[str] = None,
        function: Optional[str] = None,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Read from the page with JavaScript (`expression` or `function`), enforcing `isTrusted` rules."""
        code = expression if expression is not None else function
        if not code:
            raise ValueError("Provide `expression` (or `function`) to evaluate.")
        with fork_service.tab_scope(tab_id):
            return await work.acting(actions.evaluate, code, role=browser)

    @mcp.tool(annotations=_says("Paste text via real clipboard", destructive=True))
    async def browser_paste(
        selector: str,
        text: str,
        clear: bool = True,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Paste `text` into `selector` using the real browser clipboard and `Control+v`."""
        with fork_service.tab_scope(tab_id):
            return await work.acting(
                action_service.paste_text,
                selector,
                text,
                clear=clear,
                role=browser,
            )

    @mcp.tool(annotations=_says("Open a new tab in this browser session", destructive=True))
    async def browser_new_tab(
        url: Optional[str] = None,
        activate: bool = True,
        browser: FlexibleBrowser = None,
    ) -> str:
        """Open a new tab inside the same authenticated browser context."""
        session_id = browser or DEFAULT_BROWSER_ID
        return await work.acting(
            fork_service.new_tab,
            url=url,
            activate=activate,
            session_id=session_id,
            role=browser,
        )

    @mcp.tool(annotations=_says("List tabs in this browser session", read_only=True))
    async def browser_list_tabs(browser: FlexibleBrowser = None) -> str:
        """List all live tabs (`tabId`, `active`, `url`, `title`) in the browser session."""
        session_id = browser or DEFAULT_BROWSER_ID
        return await work.reading(
            fork_service.list_tabs,
            session_id=session_id,
            role=browser,
        )

    @mcp.tool(annotations=_says("Switch active tab in this browser session", destructive=False))
    async def browser_select_tab(
        tab_id: int,
        browser: FlexibleBrowser = None,
    ) -> str:
        """Pin `tab_id` as the active tab for subsequent commands on this browser session."""
        session_id = browser or DEFAULT_BROWSER_ID
        return await work.acting(
            fork_service.select_tab,
            tab_id,
            session_id=session_id,
            role=browser,
        )

    @mcp.tool(annotations=_says("Close a tab in this browser session", destructive=True))
    async def browser_close_tab(
        tab_id: Optional[int] = None,
        browser: FlexibleBrowser = None,
    ) -> str:
        """Close `tab_id` (or the current active tab) without closing the browser context."""
        session_id = browser or DEFAULT_BROWSER_ID
        return await work.acting(
            fork_service.close_tab,
            tab_id=tab_id,
            session_id=session_id,
            role=browser,
        )

    @mcp.tool(annotations=_says("Fork an authenticated browser context checkpoint", destructive=False))
    async def browser_fork_session(
        from_browser: FlexibleBrowser = "main",
        session_id: Optional[str] = None,
        url: Optional[str] = None,
        inherit_url: bool = False,
    ) -> str:
        """Snapshot `from_browser` cookies and `localStorage` into an isolated `BrowserContext` fork."""
        return await fork_service.fork_session(
            from_browser=from_browser or DEFAULT_BROWSER_ID,
            session_id=session_id,
            url=url,
            inherit_url=inherit_url,
        )

    @mcp.tool(annotations=_says("Promote a forked browser session back to its parent", destructive=True))
    async def browser_promote_fork(
        session_id: str,
        target_browser: FlexibleBrowser = "main",
        close_fork: bool = True,
        navigate: bool = False,
    ) -> str:
        """Promote cookies and `localStorage` from a fork back onto `target_browser`."""
        return await fork_service.promote_fork(
            session_id=session_id,
            target_browser=target_browser or DEFAULT_BROWSER_ID,
            close_fork=close_fork,
            navigate=navigate,
        )

    @mcp.tool(annotations=_says("Discard an isolated forked browser session", destructive=True))
    async def browser_discard_fork(session_id: str) -> str:
        """Close and discard an isolated fork without touching the parent session."""
        return await fork_service.discard_fork(session_id=session_id)

    @mcp.tool(annotations=_says("Scroll the page", destructive=False, read_only=False))
    async def browser_scroll(
        direction: str = "down",
        amount: int = 700,
        selector: Optional[str] = None,
        settle_ms: int = 400,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Scroll the page using the mouse wheel or scroll an element into view."""
        with fork_service.tab_scope(tab_id):
            return await work.acting(
                action_service.scroll_page,
                direction=direction,
                amount=amount,
                selector=selector,
                settle_ms=settle_ms,
                role=browser,
            )

    @mcp.tool(annotations=_says("Wait for time or selector", read_only=True))
    async def browser_wait(
        time_ms: int = 800,
        timeMs: Optional[int] = None,
        selector: Optional[str] = None,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Wait for a bounded duration in milliseconds or for a CSS `selector`."""
        wait_val = timeMs if timeMs is not None else time_ms
        with fork_service.tab_scope(tab_id):
            return await work.reading(
                action_service.wait_for_page,
                time_ms=wait_val,
                selector=selector,
                role=browser,
            )

    @mcp.tool(annotations=_says("Get session cookies and User-Agent", read_only=True))
    async def browser_get_cookies(
        url: Optional[str] = None,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Return the active context's full `Cookie` header (including `HttpOnly`) and `User-Agent`."""
        with fork_service.tab_scope(tab_id):
            return await work.reading(action_service.get_cookies, url=url, role=browser)

    @mcp.tool(
        annotations=_says("Download a URL using the browser session", destructive=False, read_only=True)
    )
    async def browser_download_url(
        url: str,
        filename: Optional[str] = None,
        browser: FlexibleBrowser = None,
        tab_id: TabIdParam = None,
    ) -> str:
        """Download a URL through the active authenticated Firefox context and save it to disk."""
        with fork_service.tab_scope(tab_id):
            return await work.reading(
                action_service.download_url,
                url=url,
                filename=filename,
                role=browser,
            )

    @mcp.tool(
        annotations=_says("Inspect tool trace analytics and streamlining opportunities", read_only=True)
    )
    async def browser_trace_summary(
        limit_recent: int = 25,
        min_sequence_count: int = 2,
        reset: bool = False,
    ) -> str:
        """Return tool call execution statistics, 2/3-step sequences, and streamlining suggestions."""
        if trace_recorder is None:
            return "{}"
        return trace_recorder.summarize_json(
            limit_recent=limit_recent,
            min_sequence_count=min_sequence_count,
            reset=reset,
        )

    registered = {
        "browser_open": browser_open,
        "browser_navigate": browser_navigate,
        "browser_read_text": browser_read_text,
        "browser_snapshot": browser_snapshot,
        "browser_read_html": browser_read_html,
        "browser_evaluate": browser_evaluate,
        "browser_paste": browser_paste,
        "browser_new_tab": browser_new_tab,
        "browser_list_tabs": browser_list_tabs,
        "browser_select_tab": browser_select_tab,
        "browser_close_tab": browser_close_tab,
        "browser_fork_session": browser_fork_session,
        "browser_promote_fork": browser_promote_fork,
        "browser_discard_fork": browser_discard_fork,
        "browser_scroll": browser_scroll,
        "browser_wait": browser_wait,
        "browser_get_cookies": browser_get_cookies,
        "browser_download_url": browser_download_url,
        "browser_trace_summary": browser_trace_summary,
    }
    if trace_recorder is not None:
        return trace_recorder.wrap_registered_tools(mcp, registered)
    return registered
