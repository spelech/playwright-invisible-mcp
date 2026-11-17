"""Isolated `BrowserContext` session wrapper created via `storage_state()` snapshots."""

from __future__ import annotations

import asyncio
import shutil
import time
from typing import Any, Awaitable, Callable, Optional

from invisible_playwright_mcp.mcp.session import StealthSession
from invisible_playwright_mcp.quiet import swallow

from .tab_routing_service import raw_live_pages


class ForkedStealthSession:
    """Isolated BrowserContext session created by snapshotting a parent session's `storage_state`."""

    def __init__(
        self,
        *,
        session_id: str,
        context: Any,
        browser: Any,
        seed: Optional[int],
        tab_resolver: Callable[[Any], Any],
        pages_describer: Callable[[Any], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self.session_id = session_id
        self._context = context
        self._browser = browser
        self._seed = seed
        self._tab_resolver = tab_resolver
        self._pages_describer = pages_describer
        self._watch: dict[int, dict[str, Any]] = {}
        self._clock = time.monotonic
        self._kept: list[str] = []

    @property
    def seed(self) -> Optional[int]:
        """Identity seed inherited from the parent session."""
        return self._seed

    def keep_until_closed(self, path: str) -> None:
        self._kept.append(path)

    def is_usable(self) -> bool:
        try:
            if self._context is None:
                return False
            if self._browser is not None and not self._browser.is_connected():
                return False
            return True
        except Exception:
            return False

    def pages(self) -> list[Any]:
        return raw_live_pages(self)

    async def new_page(self) -> Any:
        return await self._context.new_page()

    def page(self) -> Any:
        return self._tab_resolver(self)

    async def describe_pages(self) -> list[dict[str, Any]]:
        return await self._pages_describer(self)

    async def watch_frame(self, timeout: float = 3.0) -> bytes:
        page = self.page()
        key = id(page)
        state = self._watch.get(key)
        if (
            state is not None
            and state["latest"]
            and self._clock() - state["at"] > StealthSession.STALE_AFTER
        ):
            await self._stop_watch(key)
            state = None
        if state is None:
            state = {
                "page": page,
                "latest": b"",
                "arrived": asyncio.Event(),
                "at": self._clock(),
            }

            def on_frame(frame: dict[str, Any]) -> None:
                state["latest"] = frame["data"]
                state["at"] = self._clock()
                state["arrived"].set()

            await page.screencast.start(
                on_frame=on_frame,
                size=dict(StealthSession.WATCH_SIZE),
                fps=StealthSession.WATCH_FPS,
            )
            self._watch[key] = state
        if not state["latest"]:
            try:
                await asyncio.wait_for(state["arrived"].wait(), timeout)
            except asyncio.TimeoutError:
                await self._stop_watch(key)
                raise RuntimeError(
                    "the window capture started but no frame arrived in %.0f s" % timeout
                ) from None
        return state["latest"]

    async def _stop_watch(self, key: int) -> None:
        state = self._watch.pop(key, None)
        if state is None:
            return
        with swallow("the page may already be gone"):
            await state["page"].screencast.stop()

    async def close(self) -> None:
        """Close only this forked context without shutting down the shared host browser."""
        for key in list(self._watch):
            await self._stop_watch(key)
        if self._context is not None:
            with swallow("a context already gone cannot be closed twice"):
                await self._context.close()
            self._context = None
        self._forget_kept()

    def _forget_kept(self) -> None:
        for path in self._kept:
            shutil.rmtree(path, ignore_errors=True)
        self._kept.clear()
