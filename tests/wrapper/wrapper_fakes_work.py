"""In-memory StealthSession and WorkRuntime test doubles."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional

from wrapper_fakes_page import (
    FakeBrowser,
    FakeBrowserContext,
    FakeClipboard,
    FakeStealthPage,
)


class FakeStealthSession:
    """In-memory stealth browser session wrapping a `FakeStealthPage` and `FakeBrowserContext`."""

    def __init__(
        self,
        page: Optional[FakeStealthPage] = None,
        usable: bool = True,
        describe_raises: bool = False,
        browser: Optional[FakeBrowser] = None,
    ) -> None:
        self._browser = browser
        clipboard = browser._clipboard if browser is not None else FakeClipboard()
        self._context = FakeBrowserContext(clipboard=clipboard)
        first_page = page or FakeStealthPage(context=self._context)
        if first_page.context is None:
            first_page.context = self._context
        self._context.pages = [first_page]
        self._page = first_page
        self._usable = usable
        self._describe_raises = describe_raises

    def is_usable(self) -> bool:
        return self._usable

    def pages(self) -> list[FakeStealthPage]:
        return [p for p in self._context.pages if not p.is_closed()]

    async def new_page(self) -> FakeStealthPage:
        return await self._context.new_page()

    def page(self) -> FakeStealthPage:
        live = self.pages()
        if not live:
            return self._page
        return live[-1]

    async def describe_pages(self) -> list[dict[str, Any]]:
        if self._describe_raises:
            raise RuntimeError("TargetClosedError: browser engine closed")
        live = self.pages()
        return [
            {
                "active": p is (live[-1] if live else self._page),
                "url": p.url,
                "title": p.page_title,
            }
            for p in (live or [self._page])
        ]

    async def close(self) -> None:
        await self._context.close()


class FakeWorkRuntime:
    """In-memory work runtime recording lifecycle transitions for fixed and forked roles."""

    def __init__(self, default_profile_dir: str) -> None:
        self.default_profile_dir = default_profile_dir
        self._open: dict[str, Any] = {}
        self._launched: dict[str, dict[str, Any]] = {}
        self._input_locks: dict[str, asyncio.Lock] = {}
        self._typing: dict[str, Any] = {}
        self._acted: str = ""
        self.open_call_count = 0

    def roles(self) -> list[str]:
        return sorted(self._open)

    def focused(self) -> str:
        if self._acted in self._open:
            return self._acted
        return next(iter(self.roles()), "")

    def session(self, role: str) -> Any:
        s = self._open.get(role)
        if s is None or not s.is_usable():
            raise RuntimeError(f"the {role} browser is not open.")
        return s

    async def open(
        self,
        role: str = "main",
        *,
        seed: Optional[int] = None,
        proxy: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> str:
        self.open_call_count += 1
        resolved_profile = (
            str(Path(profile).expanduser().resolve())
            if profile
            else (
                str(Path(self.default_profile_dir).expanduser().resolve())
                if role == "main"
                else None
            )
        )
        resolved_seed = seed if seed is not None else 1001878902
        self._open[role] = FakeStealthSession()
        self._launched[role] = {
            "seed": resolved_seed,
            "proxy": proxy,
            "profile_dir": resolved_profile,
        }
        self._acted = role
        return f"opened {role} (seed {resolved_seed}, profile {resolved_profile})"

    async def close(self, role: str) -> str:
        existed = role in self._open
        session = self._open.pop(role, None)
        self._launched.pop(role, None)
        if session is not None and hasattr(session, "close"):
            await session.close()
        if not existed:
            return f"the {role} browser is not open."
        return f"the {role} browser is closed."

    async def close_all(self) -> None:
        for role in list(self._open):
            await self.close(role)

    async def acting(
        self,
        fn: Callable[..., Any],
        *args: Any,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        at = role or "main"
        lock = self._input_locks.setdefault(at, asyncio.Lock())
        async with lock:
            session = self.session(at)
            self._acted = at
            return await fn(session, *args, **kwargs)

    async def reading(
        self,
        fn: Callable[..., Any],
        *args: Any,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        at = role or "main"
        session = self.session(at)
        return await fn(session, *args, **kwargs)

    async def typing(
        self,
        fn: Callable[..., Any],
        selector: str,
        text: str,
        role: Optional[str] = None,
    ) -> Any:
        at = role or "main"
        lock = self._input_locks.setdefault(at, asyncio.Lock())
        async with lock:
            session = self.session(at)
            self._acted = at
            return await fn(session, selector, text)

    async def listing(self) -> dict[str, Any]:
        rows = []
        for name in self.roles():
            pages = await self._open[name].describe_pages()
            shown = next((p for p in pages if p.get("active")), pages[0] if pages else None)
            rows.append(
                {
                    "id": name,
                    "url": (shown["url"] or "") if shown else "",
                    "urls": [p.get("url", "") for p in pages],
                }
            )
        return {
            "focus": self.focused(),
            "browsers": rows,
            "note": f"{len(rows)} of 2 browsers.",
        }

    async def status(self, role: Optional[str]) -> str:
        at = role or "main"
        launched = self._launched.get(at) or {}
        page_url = self._open[at].page().url if at in self._open else "none"
        return (
            f"identity: seed {launched.get('seed')}. "
            f"profile: {launched.get('profile_dir')}. "
            f"page: {page_url}."
        )
