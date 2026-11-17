"""Enhanced browser action handlers: scoped snapshots, scroll, wait, clipboard paste, and bounded reads."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .models import (
    DEFAULT_READ_HTML_MAX_CHARS,
    DEFAULT_READ_TEXT_MAX_CHARS,
    PasteResultResponse,
    ScrollResultResponse,
    slice_bounded_text,
)
from .page_download_service import PageDownloadService


class BrowserActionService:
    """Provides stealth-compliant browser evaluation, scoped snapshots, scrolling, clipboard paste, and pagination."""

    def __init__(
        self,
        actions_module: Any,
        *,
        downloads_dir: Path = Path("/data/downloads"),
        host_downloads_prefix: Optional[str] = None,
        download_service: Optional[PageDownloadService] = None,
    ) -> None:
        self._actions = actions_module
        self._orig_evaluate: Optional[Callable[..., Any]] = getattr(actions_module, "evaluate", None)
        self._orig_snapshot: Callable[..., Any] = actions_module.snapshot
        self._orig_read_text: Optional[Callable[..., Any]] = getattr(
            actions_module, "read_text", None
        )
        self._orig_read_html: Optional[Callable[..., Any]] = getattr(
            actions_module, "read_html", None
        )
        self._download_service = download_service or PageDownloadService(
            downloads_dir=downloads_dir,
            host_downloads_prefix=host_downloads_prefix,
        )
        self._clipboard_lock = asyncio.Lock()

    async def get_cookies(self, session: Any, url: Optional[str] = None) -> str:
        """Delegate cookie inspection to `PageDownloadService`."""
        return await self._download_service.get_cookies(session, url=url)

    async def download_url(
        self,
        session: Any,
        url: str,
        filename: Optional[str] = None,
    ) -> str:
        """Delegate authenticated downloads to `PageDownloadService`."""
        return await self._download_service.download_url(session, url, filename=filename)

    async def evaluate_expression(self, session: Any, expression: str) -> str:
        """Evaluate JS while enforcing upstream `isTrusted: true` anti-bot guardrails."""
        expr = (expression or "").strip()
        if not expr:
            raise ValueError("Provide a non-empty JavaScript `expression` (or `function`).")
        self._actions._refuse_script_interaction(expr)
        page = session.page()
        result = await page.evaluate(expr)
        return json.dumps(result)

    async def scoped_snapshot(
        self,
        session: Any,
        selector: Optional[str] = None,
        viewport_only: bool = False,
        max_elements: int = 0,
    ) -> str:
        """Capture an interactive element snapshot with optional CSS bounding-box, viewport, and count filters."""
        if not selector and not viewport_only and max_elements <= 0:
            return await self._orig_snapshot(session)

        page = session.page()
        data = await page.evaluate_read(self._actions.SNAPSHOT_JS)
        elements = data.get("interactive_elements", [])

        if selector:
            try:
                bounds = await page.evaluate(
                    """(scopeSel) => {
                        const root = document.querySelector(scopeSel);
                        if (!root) return [];
                        const r = root.getBoundingClientRect();
                        return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
                    }""",
                    selector,
                )
                if bounds and len(bounds) == 4:
                    left, top, right, bottom = bounds
                    elements = [
                        e
                        for e in elements
                        if "at" in e and left <= e["at"][0] <= right and top <= e["at"][1] <= bottom
                    ]
            except Exception:
                pass

        if viewport_only:
            elements = [e for e in elements if not e.get("off_screen")]

        total_before_cap = len(elements)
        if max_elements > 0 and len(elements) > max_elements:
            elements = elements[:max_elements]
            data["truncated_from"] = total_before_cap

        data["interactive_elements"] = elements
        await self._actions._resolve_nth(page, elements)
        return json.dumps(data)

    async def scroll_page(
        self,
        session: Any,
        direction: str = "down",
        amount: int = 700,
        selector: Optional[str] = None,
        settle_ms: int = 400,
    ) -> str:
        """Scroll the active page using the mouse wheel or scroll a target selector into view."""
        page = session.page()
        if selector:
            found = await page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    return true;
                }""",
                selector,
            )
            if not found:
                return f"(no element matches {selector!r} to scroll into view)"
        else:
            dx, dy = 0, 0
            norm_dir = (direction or "down").lower().strip()
            if norm_dir == "down":
                dy = abs(amount)
            elif norm_dir == "up":
                dy = -abs(amount)
            elif norm_dir == "right":
                dx = abs(amount)
            elif norm_dir == "left":
                dx = -abs(amount)
            elif norm_dir == "top":
                await page.evaluate(
                    """() => {
                        window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
                        const inner = document.getElementById('ProdPageContent') ||
                            Array.from(document.querySelectorAll('*')).find(el => {
                                const s = getComputedStyle(el);
                                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50 && el.clientWidth > 400;
                            });
                        if (inner) inner.scrollTop = 0;
                    }"""
                )
            elif norm_dir == "bottom":
                await page.evaluate(
                    """() => {
                        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
                        const inner = document.getElementById('ProdPageContent') ||
                            Array.from(document.querySelectorAll('*')).find(el => {
                                const s = getComputedStyle(el);
                                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50 && el.clientWidth > 400;
                            });
                        if (inner) inner.scrollTop = inner.scrollHeight;
                    }"""
                )
            if dx or dy:
                try:
                    await page.mouse.wheel(dx, dy)
                except Exception:
                    await page.evaluate(f"window.scrollBy({dx}, {dy})")
                await page.evaluate(
                    f"""() => {{
                        const inner = document.getElementById('ProdPageContent') ||
                            Array.from(document.querySelectorAll('*')).find(el => {{
                                const s = getComputedStyle(el);
                                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50 && el.clientWidth > 400;
                            }});
                        if (inner && window.scrollY === 0) inner.scrollBy({dx}, {dy});
                    }}"""
                )

        if settle_ms > 0:
            await asyncio.sleep(min(settle_ms, 5000) / 1000.0)

        pos = await page.evaluate(
            """(() => {
                const inner = document.getElementById('ProdPageContent') ||
                    Array.from(document.querySelectorAll('*')).find(el => {
                        const s = getComputedStyle(el);
                        return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50 && el.clientWidth > 400;
                    });
                if (inner && window.scrollY === 0 && inner.scrollTop > 0) {
                    return {
                        scrollX: Math.round(inner.scrollLeft || 0),
                        scrollY: Math.round(inner.scrollTop),
                        scrollHeight: inner.scrollHeight,
                        innerHeight: inner.clientHeight
                    };
                }
                return {
                    scrollX: Math.round(window.scrollX),
                    scrollY: Math.round(window.scrollY),
                    scrollHeight: document.body ? document.body.scrollHeight : 0,
                    innerHeight: window.innerHeight
                };
            })()"""
        )
        return ScrollResultResponse(direction=direction, selector=selector, **pos).model_dump_json()

    async def wait_for_page(
        self,
        session: Any,
        time_ms: int = 800,
        selector: Optional[str] = None,
    ) -> str:
        """Wait for a bounded duration in milliseconds or for a CSS selector to appear."""
        page = session.page()
        if selector:
            timeout = max(1000, min(time_ms, 15000))
            await page.wait_for_selector(selector, timeout=timeout)
            return f"selector {selector!r} is present on {page.url}"
        bounded_ms = max(0, min(time_ms, 15000))
        if bounded_ms > 0:
            await asyncio.sleep(bounded_ms / 1000.0)
        return f"waited {bounded_ms}ms on {page.url}"

    async def _read_field_state(self, session: Any, page: Any, selector: str) -> dict[str, Any]:
        field_state_fn = getattr(self._actions, "_field_state", None)
        if field_state_fn is None:
            return {"value": None, "focused": True, "secret": False}
        on_selector_fn = getattr(self._actions, "_on_selector", None)
        if on_selector_fn is not None:
            return await on_selector_fn(
                session, selector, "pasting", lambda: field_state_fn(page, selector)
            )
        return await field_state_fn(page, selector)

    async def paste_text(
        self,
        session: Any,
        selector: str,
        text: str,
        clear: bool = True,
    ) -> str:
        """Write `text` to the browser clipboard and trigger a genuine `Control+v` (`isTrusted: true`) paste."""
        t0 = time.monotonic()
        page = session.page()
        timeout_ms = int(getattr(self._actions, "ACTION_TIMEOUT_MS", 5000))
        before = await self._read_field_state(session, page, selector)

        if text == "":
            await page.click(selector, timeout=timeout_ms)
            if clear:
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
        else:
            ctx = getattr(session, "_context", None)
            if ctx is None or not hasattr(ctx, "new_page"):
                raise RuntimeError(
                    "clipboard paste requires a browser context capable of opening a helper page"
                )
            async with self._clipboard_lock:
                clip_page = await ctx.new_page()
                try:
                    await clip_page.set_content(
                        "<textarea id='__pw_clip'></textarea>",
                        wait_until="domcontentloaded",
                    )
                    await clip_page.evaluate(
                        "(val) => { const el = document.getElementById('__pw_clip'); el.value = val; el.focus(); el.select(); }",
                        text,
                    )
                    await clip_page.keyboard.press("Control+c")
                finally:
                    try:
                        await clip_page.close()
                    except Exception:
                        pass

                if hasattr(page, "bring_to_front"):
                    await page.bring_to_front()
                await page.click(selector, timeout=timeout_ms)
                if clear:
                    await page.keyboard.press("Control+a")
                await page.keyboard.press("Control+v")

        after = await self._read_field_state(session, page, selector)
        kept_fn = getattr(self._actions, "what_the_field_kept", None)
        if kept_fn is not None:
            raw_msg = str(kept_fn(selector, text, before, after))
            msg = re.sub(r"\btyped\b", "pasted", raw_msg)
            msg = re.sub(r"\btyping\b", "pasting", msg)
            msg = re.sub(r"\btyping again\b", "pasting again", msg)
        else:
            msg = f"pasted {len(text)} characters into {selector}"

        retained_val = after.get("value")
        retained_chars = len(retained_val) if isinstance(retained_val, str) else 0
        matched = (
            (retained_val == text)
            if clear
            else (isinstance(retained_val, str) and text in retained_val)
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)

        return PasteResultResponse(
            selector=selector,
            pastedChars=len(text),
            retainedChars=retained_chars,
            matched=matched,
            elapsedMs=elapsed_ms,
            message=msg,
        ).model_dump_json()

    async def read_text_bounded(
        self,
        session: Any,
        selector: str = "body",
        max_chars: int = DEFAULT_READ_TEXT_MAX_CHARS,
        offset: int = 0,
    ) -> str:
        """Read visible text with bounded `max_chars` / `offset` pagination to avoid tool-output spillover."""
        if self._orig_read_text is None:
            raise RuntimeError("upstream actions.read_text is unavailable")
        full_text = str(await self._orig_read_text(session, selector))
        if full_text.startswith("(no element matches "):
            return full_text
        return slice_bounded_text(full_text, max_chars=max_chars, offset=offset)

    async def read_html_bounded(
        self,
        session: Any,
        mode: str = "form",
        max_chars: int = DEFAULT_READ_HTML_MAX_CHARS,
        offset: int = 0,
    ) -> str:
        """Read cleaned HTML markup with bounded `max_chars` / `offset` pagination."""
        if self._orig_read_html is None:
            raise RuntimeError("upstream actions.read_html is unavailable")
        full_html = str(await self._orig_read_html(session, mode))
        return slice_bounded_text(full_html, max_chars=max_chars, offset=offset)

    def install(self) -> None:
        """Install enhanced evaluate, snapshot, and bounded read handlers onto `actions`."""
        self._actions.evaluate = self.evaluate_expression
        self._actions.snapshot = self.scoped_snapshot
        if self._orig_read_text is not None:
            self._actions.read_text = self.read_text_bounded
        if self._orig_read_html is not None:
            self._actions.read_html = self.read_html_bounded

    def restore(self) -> None:
        """Restore original upstream action handlers (used by isolated test fixtures)."""
        if self._orig_evaluate is not None:
            self._actions.evaluate = self._orig_evaluate
        self._actions.snapshot = self._orig_snapshot
        if self._orig_read_text is not None:
            self._actions.read_text = self._orig_read_text
        if self._orig_read_html is not None:
            self._actions.read_html = self._orig_read_html
