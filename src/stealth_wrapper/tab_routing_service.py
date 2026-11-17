"""Same-session multi-tab ID tracking, pinned tab routing, and async task scope overrides."""

from __future__ import annotations

import contextvars
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from invisible_playwright.async_api import TargetClosedError
from invisible_playwright_mcp.mcp import actions as upstream_actions
from invisible_playwright_mcp.mcp.server import DEFAULT_BROWSER_ID
from invisible_playwright_mcp.mcp.session import StealthSession
from invisible_playwright_mcp.quiet import swallow

from .models import TabActionResponse, TabInfo, TabListResponse

_TAB_ID_OVERRIDE: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "stealthfox_tab_id_override", default=None
)


@dataclass
class _TabState:
    """Tracks monotonic tab IDs and the pinned active tab for one browser session."""

    pages: list[Any] = field(default_factory=list)
    pinned_tab_id: Optional[int] = None


def is_page_closed(page: Any) -> bool:
    """Return True if a Playwright Page reports closed or raises on inspection."""
    is_closed = getattr(page, "is_closed", None)
    if callable(is_closed):
        try:
            return bool(is_closed())
        except Exception:
            return True
    return False


def raw_live_pages(session: Any) -> list[Any]:
    """Return open Playwright Page objects backing a session's context."""
    ctx = getattr(session, "_context", None)
    if ctx is not None and hasattr(ctx, "pages"):
        return [p for p in getattr(ctx, "pages", []) if not is_page_closed(p)]
    fallback = getattr(session, "_page", None)
    if fallback is not None and not is_page_closed(fallback):
        return [fallback]
    return []


class TabRoutingService:
    """Tracks monotonic per-session tab IDs, pinned active tabs, and inline `tab_id` scopes."""

    def __init__(self, *, actions_module: Any = upstream_actions) -> None:
        self._actions = actions_module
        self._tab_states: weakref.WeakKeyDictionary[Any, _TabState] = weakref.WeakKeyDictionary()

    @contextmanager
    def tab_scope(self, tab_id: Optional[int]) -> Iterator[None]:
        """Temporarily route `session.page()` in the current async task to `tab_id`."""
        if tab_id is None:
            yield
            return
        token = _TAB_ID_OVERRIDE.set(tab_id)
        try:
            yield
        finally:
            _TAB_ID_OVERRIDE.reset(token)

    def clear_session(self, session: Any) -> None:
        """Drop tracked tab state for a closed session."""
        if session is not None:
            self._tab_states.pop(session, None)

    def clear_all(self) -> None:
        """Clear all tracked tab states."""
        self._tab_states.clear()

    def _ensure_session_bound(self, session: Any) -> None:
        if isinstance(session, StealthSession) or hasattr(session, "session_id"):
            return
        if getattr(session, "_tab_router_bound", False):
            return
        session._tab_router_bound = True
        router = self
        session.page = lambda: router.resolve_page(session)
        session.describe_pages = lambda: router.describe_session_pages(session)

    def _get_tab_state(self, session: Any) -> _TabState:
        self._ensure_session_bound(session)
        state = self._tab_states.get(session)
        if state is None:
            state = _TabState()
            self._tab_states[session] = state
        return state

    def sync_tabs(self, session: Any) -> list[tuple[int, Any]]:
        """Synchronize monotonic tab IDs against live context pages."""
        state = self._get_tab_state(session)
        live = raw_live_pages(session)

        for idx, tracked in enumerate(state.pages):
            if tracked is not None and (
                is_page_closed(tracked) or not any(tracked is lp for lp in live)
            ):
                state.pages[idx] = None

        for lp in live:
            if not any(existing is lp for existing in state.pages if existing is not None):
                state.pages.append(lp)

        return [
            (idx, p)
            for idx, p in enumerate(state.pages)
            if p is not None and not is_page_closed(p)
        ]

    def _active_tab_id(self, state: _TabState, live_pairs: list[tuple[int, Any]]) -> Optional[int]:
        if not live_pairs:
            return None
        if state.pinned_tab_id is not None:
            for tid, _page in live_pairs:
                if tid == state.pinned_tab_id:
                    return tid
        return live_pairs[-1][0]

    def resolve_page(self, session: Any) -> Any:
        """Resolve which page a tool drives, honoring `tab_id` overrides and pinned tabs."""
        live_pairs = self.sync_tabs(session)
        state = self._get_tab_state(session)

        override_id = _TAB_ID_OVERRIDE.get()
        if override_id is not None:
            for tid, page in live_pairs:
                if tid == override_id:
                    return page
            open_ids = [tid for tid, _ in live_pairs]
            raise RuntimeError(
                f"tab {override_id} is not open in this browser (open tabs: {open_ids})."
            )

        if not live_pairs:
            raise RuntimeError("this browser has no page open; browser_navigate opens one")

        if state.pinned_tab_id is not None:
            for tid, page in live_pairs:
                if tid == state.pinned_tab_id:
                    return page
            state.pinned_tab_id = live_pairs[-1][0]

        return live_pairs[-1][1]

    async def describe_session_pages(self, session: Any) -> list[dict[str, Any]]:
        """Describe every live tab with its `tabId`, `active` marker, `url`, and `title`."""
        live_pairs = self.sync_tabs(session)
        state = self._get_tab_state(session)
        active_id = self._active_tab_id(state, live_pairs)
        out: list[dict[str, Any]] = []
        for tid, page in live_pairs:
            row: dict[str, Any] = {
                "tabId": tid,
                "active": tid == active_id,
                "url": "",
                "title": "",
            }
            with swallow("a page that cannot say where it is answers blank"):
                row["url"] = getattr(page, "url", "") or ""
            with swallow(
                "a page mid-navigation cannot say its title",
                unless=(TargetClosedError,),
            ):
                if hasattr(page, "title"):
                    row["title"] = await page.title()
            out.append(row)
        return out

    async def new_tab(
        self,
        session: Any,
        url: Optional[str] = None,
        activate: bool = True,
        session_id: str = DEFAULT_BROWSER_ID,
    ) -> str:
        """Open a new tab in the current browser context without losing existing tab state."""
        state = self._get_tab_state(session)
        live_before = self.sync_tabs(session)
        prev_active_id = self._active_tab_id(state, live_before)
        prev_active_page = next((p for tid, p in live_before if tid == prev_active_id), None)

        if not activate and state.pinned_tab_id is None and prev_active_id is not None:
            state.pinned_tab_id = prev_active_id

        if hasattr(session, "new_page"):
            page = await session.new_page()
        else:
            page = await session._context.new_page()

        live_after = self.sync_tabs(session)
        new_tab_id = next(
            (tid for tid, p in live_after if p is page),
            live_after[-1][0] if live_after else 0,
        )

        if activate:
            state.pinned_tab_id = new_tab_id
            if hasattr(page, "bring_to_front"):
                await page.bring_to_front()
        elif prev_active_page is not None and hasattr(prev_active_page, "bring_to_front"):
            with swallow("previous tab focus restoration is best-effort"):
                await prev_active_page.bring_to_front()

        if url:
            with self.tab_scope(new_tab_id):
                await self._actions.navigate(session, url, wait_until="domcontentloaded")

        active_id = self._active_tab_id(state, self.sync_tabs(session))
        title = ""
        with swallow("title read on a newly created tab is best-effort"):
            if hasattr(page, "title"):
                title = await page.title()

        return TabActionResponse(
            status="created",
            sessionId=session_id,
            tabId=new_tab_id,
            activeTabId=active_id,
            active=(new_tab_id == active_id),
            url=getattr(page, "url", "") or "",
            title=title,
            tabCount=len(self.sync_tabs(session)),
        ).model_dump_json()

    async def list_tabs(self, session: Any, session_id: str = DEFAULT_BROWSER_ID) -> str:
        """List all live tabs (`tabId`, `active`, `url`, `title`) in the browser context."""
        live_pairs = self.sync_tabs(session)
        state = self._get_tab_state(session)
        active_id = self._active_tab_id(state, live_pairs)
        rows = await self.describe_session_pages(session)
        tabs = [TabInfo(**row) for row in rows]
        return TabListResponse(
            sessionId=session_id,
            activeTabId=active_id,
            tabCount=len(tabs),
            tabs=tabs,
        ).model_dump_json()

    async def select_tab(
        self, session: Any, tab_id: int, session_id: str = DEFAULT_BROWSER_ID
    ) -> str:
        """Switch the pinned active tab for subsequent commands on this browser session."""
        live_pairs = self.sync_tabs(session)
        target = next((p for tid, p in live_pairs if tid == tab_id), None)
        if target is None:
            open_ids = [tid for tid, _ in live_pairs]
            raise ValueError(
                f"tab {tab_id} does not exist in {session_id!r} (open tabs: {open_ids})."
            )
        state = self._get_tab_state(session)
        state.pinned_tab_id = tab_id
        if hasattr(target, "bring_to_front"):
            await target.bring_to_front()

        title = ""
        with swallow("title read on selected tab is best-effort"):
            if hasattr(target, "title"):
                title = await target.title()

        return TabActionResponse(
            status="selected",
            sessionId=session_id,
            tabId=tab_id,
            activeTabId=tab_id,
            active=True,
            url=getattr(target, "url", "") or "",
            title=title,
            tabCount=len(live_pairs),
        ).model_dump_json()

    async def close_tab(
        self,
        session: Any,
        tab_id: Optional[int] = None,
        session_id: str = DEFAULT_BROWSER_ID,
    ) -> str:
        """Close `tab_id` (or the current active tab) without closing the browser context."""
        live_pairs = self.sync_tabs(session)
        if not live_pairs:
            raise ValueError(f"{session_id!r} has no open tabs to close.")

        state = self._get_tab_state(session)
        target_id = tab_id if tab_id is not None else self._active_tab_id(state, live_pairs)
        target_page = next((p for tid, p in live_pairs if tid == target_id), None)
        if target_page is None or target_id is None:
            open_ids = [tid for tid, _ in live_pairs]
            raise ValueError(
                f"tab {target_id} does not exist in {session_id!r} (open tabs: {open_ids})."
            )

        if hasattr(target_page, "close"):
            await target_page.close()
        if 0 <= target_id < len(state.pages):
            state.pages[target_id] = None

        remaining = self.sync_tabs(session)
        if state.pinned_tab_id == target_id or state.pinned_tab_id is None:
            state.pinned_tab_id = remaining[-1][0] if remaining else None

        active_id = self._active_tab_id(state, remaining)
        active_page = next((p for tid, p in remaining if tid == active_id), None)
        if active_page is not None and hasattr(active_page, "bring_to_front"):
            with swallow("focusing fallback tab is best-effort"):
                await active_page.bring_to_front()

        return TabActionResponse(
            status="closed",
            sessionId=session_id,
            tabId=target_id,
            activeTabId=active_id,
            active=False,
            url=getattr(active_page, "url", "") if active_page is not None else "",
            tabCount=len(remaining),
        ).model_dump_json()
