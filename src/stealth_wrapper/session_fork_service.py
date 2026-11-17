"""Isolated BrowserContext forking via `storage_state()` snapshots and session lifecycle hooks."""

from __future__ import annotations

import asyncio
import os
import re
import time
import urllib.parse
from contextlib import AbstractContextManager
from typing import Any, Awaitable, Callable, Optional

from invisible_playwright.async_api import InvisiblePlaywright
from invisible_playwright_mcp.mcp import actions as upstream_actions
from invisible_playwright_mcp.mcp.server import DEFAULT_BROWSER_ID, SUPPORT_BROWSER_ID
from invisible_playwright_mcp.mcp.session import StealthSession
from invisible_playwright_mcp.quiet import swallow

from .models import (
    ForkCreateResponse,
    ForkDiscardResponse,
    ForkPromoteResponse,
    sanitize_cookies,
)
from .tab_routing_service import TabRoutingService

_FORK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


from .forked_session import ForkedStealthSession


class SessionForkService:
    """Coordinates lightweight `storage_state` context forks and delegates multi-tab routing."""

    def __init__(
        self,
        work_instance: Any,
        *,
        actions_module: Any = upstream_actions,
        tab_service: Optional[TabRoutingService] = None,
        host_browser_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._work = work_instance
        self._actions = actions_module
        self._tabs = tab_service or TabRoutingService(actions_module=actions_module)
        self._host_browser_factory = host_browser_factory
        self._fork_meta: dict[str, dict[str, Any]] = {}
        self._fork_counter = 0
        self._host_lock = asyncio.Lock()
        self._host_ipw: Any = None
        self._host_browser: Any = None
        self._orig_session_page: Optional[Callable[[Any], Any]] = None
        self._orig_describe_pages: Optional[Callable[[Any], Awaitable[list[dict[str, Any]]]]] = None
        self._orig_close: Optional[Callable[[str], Awaitable[str]]] = None
        self._orig_close_all: Optional[Callable[[], Awaitable[None]]] = None
        self._orig_listing: Optional[Callable[[], Awaitable[dict[str, Any]]]] = None
        self._installed = False

    def tab_scope(self, tab_id: Optional[int]) -> AbstractContextManager[None]:
        """Temporarily route `session.page()` in the current async task to `tab_id`."""
        return self._tabs.tab_scope(tab_id)

    async def new_tab(
        self,
        session: Any,
        url: Optional[str] = None,
        activate: bool = True,
        session_id: str = DEFAULT_BROWSER_ID,
    ) -> str:
        """Open a new tab in the current browser context."""
        return await self._tabs.new_tab(session, url=url, activate=activate, session_id=session_id)

    async def list_tabs(self, session: Any, session_id: str = DEFAULT_BROWSER_ID) -> str:
        """List all live tabs in the current browser context."""
        return await self._tabs.list_tabs(session, session_id=session_id)

    async def select_tab(
        self, session: Any, tab_id: int, session_id: str = DEFAULT_BROWSER_ID
    ) -> str:
        """Switch the pinned active tab for subsequent commands."""
        return await self._tabs.select_tab(session, tab_id, session_id=session_id)

    async def close_tab(
        self,
        session: Any,
        tab_id: Optional[int] = None,
        session_id: str = DEFAULT_BROWSER_ID,
    ) -> str:
        """Close `tab_id` (or the active tab) without closing the browser context."""
        return await self._tabs.close_tab(session, tab_id=tab_id, session_id=session_id)

    def _next_fork_id(self) -> str:
        while True:
            self._fork_counter += 1
            candidate = f"fork-{self._fork_counter}"
            if candidate not in self._work._open and candidate not in self._fork_meta:
                return candidate

    async def _ensure_host_browser(
        self, parent_session: Any, parent_launched: dict[str, Any]
    ) -> Any:
        parent_browser = getattr(parent_session, "_browser", None)
        if parent_browser is not None:
            try:
                if parent_browser.is_connected():
                    return parent_browser
            except Exception:
                pass

        async with self._host_lock:
            if self._host_browser is not None:
                try:
                    if self._host_browser.is_connected():
                        return self._host_browser
                except Exception:
                    pass
                await self._close_host_browser_unlocked()

            headless = parent_launched.get(
                "headless", os.environ.get("STEALTHFOX_HEADLESS", "1") != "0"
            )
            host_kwargs: dict[str, Any] = {"headless": headless}
            seed = parent_launched.get("seed")
            if seed is None:
                seed = getattr(parent_session, "seed", None)
            if seed is not None:
                host_kwargs["seed"] = seed
            if parent_launched.get("proxy"):
                host_kwargs["proxy"] = parent_launched["proxy"]
            binary_path = parent_launched.get("binary_path") or getattr(
                getattr(self._work, "_engine", None), "binary_path", None
            )
            if binary_path:
                host_kwargs["binary_path"] = binary_path

            if self._host_browser_factory is not None:
                self._host_browser = self._host_browser_factory(**host_kwargs)
                return self._host_browser

            self._host_ipw = InvisiblePlaywright(**host_kwargs)
            entered = await self._host_ipw.__aenter__()
            if not hasattr(entered, "new_context"):
                raise RuntimeError(
                    "shared fork host browser launched without new_context() capability"
                )
            self._host_browser = entered
            return self._host_browser

    async def _close_host_browser_unlocked(self) -> None:
        self._host_browser = None
        if self._host_ipw is not None:
            with swallow("host browser shutdown is best-effort"):
                await self._host_ipw.__aexit__(None, None, None)
            self._host_ipw = None

    async def fork_session(
        self,
        from_browser: str = DEFAULT_BROWSER_ID,
        session_id: Optional[str] = None,
        url: Optional[str] = None,
        inherit_url: bool = False,
    ) -> str:
        """Fork an authenticated browser context via `storage_state()` in `<200ms`."""
        source_role = from_browser or DEFAULT_BROWSER_ID
        if source_role not in self._work._open or not self._work._open[source_role].is_usable():
            if source_role in (DEFAULT_BROWSER_ID, SUPPORT_BROWSER_ID):
                await self._work.open(source_role)
            else:
                raise RuntimeError(
                    f"parent browser {source_role!r} is not open; open or fork it first."
                )

        if session_id is None:
            fork_id = self._next_fork_id()
        else:
            fork_id = session_id.strip()
            if not fork_id:
                raise ValueError("Provide a non-empty `session_id` or omit it to auto-name.")
            if fork_id in (DEFAULT_BROWSER_ID, SUPPORT_BROWSER_ID):
                raise ValueError(
                    f"cannot use reserved browser role {fork_id!r} as a fork session_id."
                )
            if not _FORK_ID_RE.match(fork_id):
                raise ValueError(
                    "invalid `session_id`: use 1-64 alphanumeric characters, `.`, `_`, or `-`."
                )
            if fork_id in self._work._open:
                raise ValueError(
                    f"session {fork_id!r} is already open; choose another `session_id` or discard it first."
                )

        parent_session = self._work._open[source_role]
        parent_launched = self._work._launched.get(source_role) or {}
        parent_url = ""
        try:
            parent_url = str(getattr(parent_session.page(), "url", "") or "")
        except Exception:
            pass

        t0 = time.monotonic()
        storage_state = await parent_session._context.storage_state()
        raw_cookies = list(storage_state.get("cookies") or [])
        origins = list(storage_state.get("origins") or [])
        clean_cookies = sanitize_cookies(raw_cookies)
        host_browser = await self._ensure_host_browser(parent_session, parent_launched)
        fork_context = await host_browser.new_context(
            storage_state={"cookies": clean_cookies, "origins": origins}
        )
        if clean_cookies and hasattr(fork_context, "add_cookies"):
            existing_cookies = []
            try:
                existing_cookies = await fork_context.cookies()
            except Exception:
                existing_cookies = []
            if len(existing_cookies) < len(clean_cookies):
                await fork_context.add_cookies(clean_cookies)
        page = await fork_context.new_page()

        fork_session = ForkedStealthSession(
            session_id=fork_id,
            context=fork_context,
            browser=host_browser,
            seed=parent_launched.get("seed") or getattr(parent_session, "seed", None),
            tab_resolver=self._tabs.resolve_page,
            pages_describer=self._tabs.describe_session_pages,
        )
        self._tabs.sync_tabs(fork_session)
        fork_ms = round((time.monotonic() - t0) * 1000.0, 2)

        launched_meta = dict(parent_launched)
        launched_meta["profile_dir"] = None
        launched_meta["forked_from"] = source_role

        self._work._open[fork_id] = fork_session
        self._work._launched[fork_id] = launched_meta
        self._work._acted = fork_id
        self._fork_meta[fork_id] = {
            "from_browser": source_role,
            "parent_url": parent_url,
            "created_at": time.monotonic(),
        }

        target_url = url
        if not target_url and inherit_url and parent_url.startswith(("http://", "https://")):
            target_url = parent_url

        if target_url:
            await self._work.acting(
                self._actions.navigate,
                target_url,
                wait_until="domcontentloaded",
                role=fork_id,
            )

        live_pairs = self._tabs.sync_tabs(fork_session)
        first_tab_id = live_pairs[0][0] if live_pairs else 0
        final_url = str(getattr(fork_session.page(), "url", "") or getattr(page, "url", "") or "")

        return ForkCreateResponse(
            status="forked",
            sessionId=fork_id,
            fromBrowser=source_role,
            url=final_url,
            parentUrl=parent_url,
            cookieCount=len(clean_cookies),
            originCount=len(origins),
            forkDurationMs=fork_ms,
            tabId=first_tab_id,
        ).model_dump_json()

    async def promote_fork(
        self,
        session_id: str,
        target_browser: str = DEFAULT_BROWSER_ID,
        close_fork: bool = True,
        navigate: bool = False,
    ) -> str:
        """Promote cookies and `localStorage` from a successful fork back into `target_browser`."""
        fork_id = (session_id or "").strip()
        if not fork_id or fork_id in (DEFAULT_BROWSER_ID, SUPPORT_BROWSER_ID):
            raise ValueError(
                f"{session_id!r} is not a forked session ID; pass a fork created by `browser_fork_session`."
            )
        fork_session = self._work._open.get(fork_id)
        if fork_session is None or not fork_session.is_usable():
            raise RuntimeError(f"forked session {fork_id!r} is not open.")

        target_id = target_browser or DEFAULT_BROWSER_ID
        target_session = self._work._open.get(target_id)
        if target_session is None or not target_session.is_usable():
            raise RuntimeError(f"target browser {target_id!r} is not open.")

        fork_state = await fork_session._context.storage_state()
        fork_url = ""
        try:
            fork_url = str(getattr(fork_session.page(), "url", "") or "")
        except Exception:
            pass

        cookies = sanitize_cookies(list(fork_state.get("cookies") or []))
        if cookies and hasattr(target_session._context, "add_cookies"):
            await target_session._context.add_cookies(cookies)

        origins = list(fork_state.get("origins") or [])
        origins_synced = 0
        target_page = None
        try:
            target_page = target_session.page()
        except Exception:
            target_page = None

        if target_page is not None and origins:
            target_url = str(getattr(target_page, "url", "") or "")
            parsed = urllib.parse.urlsplit(target_url)
            target_origin = (
                f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
            )
            for origin_entry in origins:
                if origin_entry.get("origin") == target_origin:
                    items = origin_entry.get("localStorage") or []
                    if items:
                        await target_page.evaluate(
                            """(entries) => {
                                for (const item of entries) {
                                    if (item && typeof item.name === 'string') {
                                        window.localStorage.setItem(item.name, String(item.value ?? ''));
                                    }
                                }
                            }""",
                            items,
                        )
                    origins_synced += 1

        did_navigate = False
        if navigate and fork_url.startswith(("http://", "https://")):
            await self._work.acting(
                self._actions.navigate,
                fork_url,
                wait_until="domcontentloaded",
                role=target_id,
            )
            did_navigate = True

        if close_fork:
            await self._close_session_by_id(fork_id)

        return ForkPromoteResponse(
            status="promoted",
            sessionId=fork_id,
            targetBrowser=target_id,
            promotedUrl=fork_url,
            cookiesApplied=len(cookies),
            originsSynced=origins_synced,
            forkClosed=close_fork,
            navigated=did_navigate,
        ).model_dump_json()

    async def discard_fork(self, session_id: str) -> str:
        """Discard an isolated fork without modifying its parent checkpoint."""
        fork_id = (session_id or "").strip()
        if not fork_id or fork_id in (DEFAULT_BROWSER_ID, SUPPORT_BROWSER_ID):
            raise ValueError(
                f"refused: {session_id!r} cannot be discarded as a fork; use `browser_close` for `main`/`support`."
            )
        meta = self._fork_meta.get(fork_id) or {}
        if fork_id not in self._work._open:
            raise RuntimeError(f"forked session {fork_id!r} is not open.")
        await self._close_session_by_id(fork_id)
        return ForkDiscardResponse(
            status="discarded",
            sessionId=fork_id,
            fromBrowser=meta.get("from_browser"),
            openBrowsers=sorted(self._work._open),
        ).model_dump_json()

    async def _close_session_by_id(self, session_id: str) -> None:
        self._tabs.clear_session(self._work._open.get(session_id))
        self._fork_meta.pop(session_id, None)
        if self._orig_close is not None:
            await self._orig_close(session_id)
        else:
            rec = (
                self._work._typing.pop(session_id, None) if hasattr(self._work, "_typing") else None
            )
            if rec is not None:
                rec.task.cancel()
            popped = self._work._open.pop(session_id, None)
            self._work._launched.pop(session_id, None)
            if popped is not None and hasattr(popped, "close"):
                await popped.close()
        if hasattr(self._work, "_input_locks"):
            self._work._input_locks.pop(session_id, None)

    def install(self) -> None:
        """Install multi-tab page resolution and fork lifecycle cleanup onto upstream classes."""
        if self._installed:
            return
        self._installed = True
        svc = self
        tabs = self._tabs

        self._orig_session_page = StealthSession.page
        self._orig_describe_pages = StealthSession.describe_pages

        def _managed_page(session_self: Any) -> Any:
            return tabs.resolve_page(session_self)

        async def _managed_describe_pages(session_self: Any) -> list[dict[str, Any]]:
            return await tabs.describe_session_pages(session_self)

        StealthSession.page = _managed_page  # type: ignore[method-assign]
        StealthSession.describe_pages = _managed_describe_pages  # type: ignore[method-assign]

        if hasattr(self._work, "close"):
            orig_close = self._work.close
            self._orig_close = orig_close

            async def _managed_close(role: str) -> str:
                tabs.clear_session(svc._work._open.get(role))
                svc._fork_meta.pop(role, None)
                res = await orig_close(role)
                if hasattr(svc._work, "_input_locks"):
                    svc._work._input_locks.pop(role, None)
                return res

            self._work.close = _managed_close

        if hasattr(self._work, "close_all"):
            orig_close_all = self._work.close_all
            self._orig_close_all = orig_close_all

            async def _managed_close_all() -> None:
                await orig_close_all()
                tabs.clear_all()
                svc._fork_meta.clear()
                async with svc._host_lock:
                    await svc._close_host_browser_unlocked()

            self._work.close_all = _managed_close_all

        if hasattr(self._work, "listing"):
            orig_listing = self._work.listing
            self._orig_listing = orig_listing

            async def _managed_listing() -> dict[str, Any]:
                data = await orig_listing()
                rows = data.get("browsers") or []
                forks = [
                    r["id"]
                    for r in rows
                    if r.get("id") not in (DEFAULT_BROWSER_ID, SUPPORT_BROWSER_ID)
                ]
                if forks:
                    data["note"] = (
                        f"{len(rows)} browser sessions open (forks: {', '.join(forks)}). "
                        f"Commands that name none go to {DEFAULT_BROWSER_ID}."
                    )
                return data

            self._work.listing = _managed_listing

    def restore(self) -> None:
        """Restore original upstream StealthSession and work methods (used by tests)."""
        if not self._installed:
            return
        self._installed = False
        if self._orig_session_page is not None:
            StealthSession.page = self._orig_session_page  # type: ignore[method-assign]
        if self._orig_describe_pages is not None:
            StealthSession.describe_pages = self._orig_describe_pages  # type: ignore[method-assign]
        if self._orig_close is not None:
            self._work.close = self._orig_close
        if self._orig_close_all is not None:
            self._work.close_all = self._orig_close_all
        if self._orig_listing is not None:
            self._work.listing = self._orig_listing
