"""In-memory Playwright browser, context, keyboard, mouse, and page test doubles."""

from __future__ import annotations

import base64
import urllib.parse
from pathlib import Path
from typing import Any, Optional


class FakeClipboard:
    """In-memory browser clipboard shared across pages within a fake context."""

    def __init__(self) -> None:
        self.text: str = ""


class FakeKeyboard:
    """In-memory keyboard controller supporting Ctrl+A, Ctrl+C, Ctrl+V, and Backspace."""

    def __init__(self, page: FakeStealthPage, clipboard: FakeClipboard) -> None:
        self._page = page
        self._clipboard = clipboard
        self.pressed_keys: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed_keys.append(key)
        sel = self._page._focused_selector
        if key == "Control+a":
            self._page._selected_all = True
            return
        if key == "Control+c":
            if sel and sel in self._page.field_values:
                self._clipboard.text = self._page.field_values[sel]
            return
        if key == "Backspace":
            if sel and sel in self._page.field_values:
                if self._page._selected_all:
                    self._page.field_values[sel] = ""
                    self._page._selected_all = False
                else:
                    self._page.field_values[sel] = self._page.field_values[sel][:-1]
            return
        if key == "Control+v":
            if sel is not None:
                pasted = self._clipboard.text
                if self._page._selected_all:
                    self._page.field_values[sel] = pasted
                    self._page._selected_all = False
                else:
                    self._page.field_values[sel] = self._page.field_values.get(sel, "") + pasted
                self._page.events.append({"type": "paste", "isTrusted": True, "len": len(pasted)})
                self._page.events.append(
                    {
                        "type": "input",
                        "isTrusted": True,
                        "inputType": "insertFromPaste",
                    }
                )


class FakeLocator:
    """In-memory locator supporting `first` and `all_inner_texts`."""

    def __init__(self, page: FakeStealthPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> FakeLocator:
        return self

    async def all_inner_texts(self) -> list[str]:
        if self._selector == "#missing":
            return []
        if self._selector in self._page.selector_texts:
            return [self._page.selector_texts[self._selector]]
        return [self._page.body_text]

    async def evaluate(self, _expression: str, timeout: int = 5000) -> dict[str, Any]:
        del timeout
        return {
            "value": self._page.field_values.get(self._selector, ""),
            "focused": self._page._focused_selector == self._selector,
            "secret": False,
        }


class FakeBrowser:
    """In-memory browser capable of creating isolated contexts from `storage_state`."""

    def __init__(self, clipboard: Optional[FakeClipboard] = None) -> None:
        self.connected = True
        self.contexts: list[FakeBrowserContext] = []
        self._clipboard = clipboard or FakeClipboard()

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(
        self, storage_state: Optional[dict[str, Any]] = None
    ) -> FakeBrowserContext:
        state = storage_state or {}
        cookies = [dict(c) for c in state.get("cookies", [])]
        origins_map: dict[str, dict[str, str]] = {}
        for origin_entry in state.get("origins", []):
            origin = origin_entry.get("origin")
            if origin:
                origins_map[origin] = {
                    item["name"]: item["value"] for item in origin_entry.get("localStorage", [])
                }
        ctx = FakeBrowserContext(
            initial_cookies=cookies,
            initial_local_storage=origins_map,
            initial_pages=[],
            clipboard=self._clipboard,
        )
        self.contexts.append(ctx)
        return ctx


class FakeBrowserContext:
    """In-memory browser context holding cookies, origins `localStorage`, and live pages."""

    def __init__(
        self,
        initial_cookies: Optional[list[dict[str, Any]]] = None,
        initial_local_storage: Optional[dict[str, dict[str, str]]] = None,
        initial_pages: Optional[list[FakeStealthPage]] = None,
        clipboard: Optional[FakeClipboard] = None,
    ) -> None:
        self._clipboard = clipboard or FakeClipboard()
        self._cookies: list[dict[str, Any]] = (
            list(initial_cookies)
            if initial_cookies is not None
            else [
                {
                    "name": "StayLoggedIn",
                    "value": "true",
                    "domain": ".mcmaster.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "expires": 1893456000.0,
                },
                {
                    "name": "ak_bmsc",
                    "value": "stealth-akamai-token",
                    "domain": ".mcmaster.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "expires": 1893456000.0,
                },
            ]
        )
        self._local_storage: dict[str, dict[str, str]] = dict(
            initial_local_storage
            if initial_local_storage is not None
            else {"https://www.mcmaster.com": {"CartCheckpoint": "v1"}}
        )
        self.pages: list[FakeStealthPage] = list(initial_pages or [])
        self.closed = False

    async def new_page(self) -> FakeStealthPage:
        page = FakeStealthPage(url="about:blank", context=self)
        self.pages.append(page)
        return page

    async def cookies(self, urls: Optional[list[str]] = None) -> list[dict[str, Any]]:
        del urls
        return [dict(c) for c in self._cookies]

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        indexed = {(c.get("name"), c.get("domain"), c.get("path")): dict(c) for c in self._cookies}
        for c in cookies:
            indexed[(c.get("name"), c.get("domain"), c.get("path"))] = dict(c)
        self._cookies = list(indexed.values())

    async def storage_state(self) -> dict[str, Any]:
        return {
            "cookies": [dict(c) for c in self._cookies],
            "origins": [
                {
                    "origin": origin,
                    "localStorage": [{"name": k, "value": v} for k, v in pairs.items()],
                }
                for origin, pairs in self._local_storage.items()
            ],
        }

    async def close(self) -> None:
        self.closed = True
        for p in list(self.pages):
            p._closed = True


class FakeMouse:
    """In-memory mouse wheel state tracker."""

    def __init__(self, page: FakeStealthPage) -> None:
        self._page = page
        self.fail_wheel = False

    async def wheel(self, dx: int, dy: int) -> None:
        if self.fail_wheel:
            raise RuntimeError("wheel input unavailable")
        self._page.scroll_x += int(dx)
        self._page.scroll_y = max(0, self._page.scroll_y + int(dy))


class FakeStealthPage:
    """In-memory Playwright page harness recording state transitions without external network I/O."""

    def __init__(
        self,
        url: str = "https://www.mcmaster.com/5278N42/",
        context: Optional[FakeBrowserContext] = None,
    ) -> None:
        self.url = url
        self.page_title = "McMaster-Carr Test Catalog"
        self.context = context
        self._closed = False
        self.brought_to_front_count = 0
        self.scroll_x = 0
        self.scroll_y = 0
        self.scroll_height = 3200
        self.inner_height = 800
        self.resource_count = 4
        self.text_len = 250
        self.elem_count = 24
        self.text_hash = 1042
        self.mutate_calls_remaining = 0
        self.waited_selectors: list[tuple[str, int]] = []
        self.mouse = FakeMouse(self)
        clipboard = context._clipboard if context is not None else FakeClipboard()
        self.keyboard = FakeKeyboard(self, clipboard)
        self.field_values: dict[str, str] = {"#bom-input": "old-bom-text"}
        self._focused_selector: Optional[str] = None
        self._selected_all: bool = False
        self.events: list[dict[str, Any]] = []
        self.body_text: str = "McMaster-Carr catalog default page text."
        self.body_html: str = "<main><h1>McMaster-Carr</h1></main>"
        self.selector_texts: dict[str, str] = {}
        self.selector_bounds: dict[str, list[int]] = {
            "#main-catalog": [0, 0, 900, 600],
            "#empty-box": [2000, 2000, 2100, 2100],
        }
        self.snapshot_elements: list[dict[str, Any]] = [
            {"tag": "a", "selector": "a[href='/part-1']", "text": "Part 1", "at": [120, 180]},
            {
                "tag": "button",
                "selector": "#add-to-order",
                "text": "Add to Order",
                "at": [450, 320],
            },
            {
                "tag": "a",
                "selector": "a[href='/footer-link']",
                "text": "Footer Terms",
                "at": [150, 1400],
                "off_screen": True,
            },
        ]
        self.download_payloads: dict[str, bytes] = {
            "https://www.mcmaster.com/mvC/Library/CAD2/5278N42.STEP": (
                b"ISO-10303-21;\r\nHEADER;\r\nFILE_DESCRIPTION(('STEP AP214'),'1');\r\n"
                b"ENDSEC;\r\nDATA;\r\nENDSEC;\r\nEND-ISO-10303-21;\r\n"
            ),
            "https://www.mcmaster.com/mvD/Library/CAD2/97355A439.STEP": (
                b"<!-- v8rD4psGZgdyDFgMSPfSpE825MCKHmp8wxErRawf -->"
            ),
        }
        self.cad_control_enabled: bool = False
        self.cad_step_selected: bool = False
        self.clicked_selectors: list[str] = []

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True

    async def bring_to_front(self) -> None:
        self.brought_to_front_count += 1

    async def title(self) -> str:
        return self.page_title

    async def set_content(self, html: str, wait_until: str = "domcontentloaded") -> None:
        del wait_until
        self.body_html = html
        if "__pw_clip" in html:
            self.field_values["#__pw_clip"] = ""

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def click(self, selector: str, timeout: int = 5000) -> None:
        del timeout
        self.clicked_selectors.append(selector)
        self._focused_selector = selector
        self._selected_all = False
        if selector == "[id^='dropdown-3-D STEP/']":
            self.cad_step_selected = True

    def expect_download(self, timeout: int = 15000) -> Any:
        del timeout
        payload = self.download_payloads["https://www.mcmaster.com/mvC/Library/CAD2/5278N42.STEP"]

        class _FakeDownload:
            async def save_as(self, path: str) -> None:
                Path(path).write_bytes(payload)

        class _FakeDownloadCtx:
            @property
            async def value(self) -> _FakeDownload:
                return _FakeDownload()

            async def __aenter__(self) -> _FakeDownloadCtx:
                return self

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

        return _FakeDownloadCtx()

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        expr = expression.strip()
        if "__pw_clip" in expr and arg is not None:
            self.field_values["#__pw_clip"] = str(arg)
            self._focused_selector = "#__pw_clip"
            self._selected_all = True
            return None

        if "window.localStorage.setItem" in expr and isinstance(arg, list):
            parsed = urllib.parse.urlsplit(self.url)
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
            if self.context is not None and origin:
                store = self.context._local_storage.setdefault(origin, {})
                for item in arg:
                    store[str(item["name"])] = str(item.get("value", ""))
            return None

        if "a[href*='/Library/CAD2/']" in expr and "hasCadControl" in expr:
            if not self.cad_control_enabled:
                return {"hasCadControl": False}
            return {
                "hasCadControl": True,
                "hasCombo": True,
                "href": (
                    "/mvD/Library/CAD2/97355A439.STEP"
                    if self.cad_step_selected
                    else "/mvD/Library/CAD2/97355A439.SLDPRT"
                ),
                "needsStepSelect": bool(arg and not self.cad_step_selected),
            }

        if "dropdown-3-D STEP/" in expr and expr.startswith("Boolean("):
            return False

        if "a[href*='/Library/CAD2/']" in expr and "new URL" in expr:
            return "https://www.mcmaster.com/mvD/Library/CAD2/97355A439.STEP"

        if "performance.getEntriesByType('resource').length" in expr and "innerText" in expr:
            if self.mutate_calls_remaining > 0:
                self.mutate_calls_remaining -= 1
                self.resource_count += 1
                self.text_len += 25
                self.text_hash += 7
            return f"{self.resource_count}:{self.text_len}:{self.elem_count}:{self.text_hash}"

        if expr == "performance.getEntriesByType('resource').length":
            return self.resource_count

        if expr == "navigator.userAgent":
            return "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"

        if "getBoundingClientRect" in expr and arg is not None:
            return self.selector_bounds.get(str(arg), [])

        if "scrollIntoView" in expr and arg is not None:
            if str(arg) in self.selector_bounds:
                bounds = self.selector_bounds[str(arg)]
                self.scroll_y = bounds[1]
                return True
            return False

        if "window.scrollTo({ top: 0" in expr:
            self.scroll_x = 0
            self.scroll_y = 0
            return None

        if "window.scrollTo({ top: document.body.scrollHeight" in expr:
            self.scroll_y = self.scroll_height
            return None

        if expr.startswith("window.scrollBy("):
            inside = expr[len("window.scrollBy(") : -1]
            dx_str, dy_str = inside.split(",")
            self.scroll_x += int(dx_str.strip())
            self.scroll_y = max(0, self.scroll_y + int(dy_str.strip()))
            return None

        if "scrollX: Math.round(window.scrollX)" in expr:
            return {
                "scrollX": self.scroll_x,
                "scrollY": self.scroll_y,
                "scrollHeight": self.scroll_height,
                "innerHeight": self.inner_height,
            }

        if "fetch(targetUrl" in expr and arg is not None:
            target_url = str(arg)
            payload = self.download_payloads.get(target_url)
            if payload is None:
                return {
                    "ok": False,
                    "status": 403,
                    "statusText": "Forbidden",
                    "contentType": "text/html",
                    "byteLength": 0,
                    "base64": "",
                }
            return {
                "ok": True,
                "status": 200,
                "statusText": "OK",
                "contentType": "application/STEP",
                "byteLength": len(payload),
                "base64": base64.b64encode(payload).decode("ascii"),
            }

        return {"evaluated": expr}

    async def evaluate_read(self, _expression: str) -> dict[str, Any]:
        return {
            "title": self.page_title,
            "url": self.url,
            "interactive_elements": [dict(e) for e in self.snapshot_elements],
        }

    async def wait_for_selector(self, selector: str, timeout: int = 15000) -> None:
        self.waited_selectors.append((selector, timeout))
