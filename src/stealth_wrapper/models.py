"""Shared Pydantic response models and state sanitization utilities for stealth_wrapper."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

DEFAULT_READ_TEXT_MAX_CHARS = 8000
DEFAULT_READ_HTML_MAX_CHARS = 12000


class CookieRecord(BaseModel):
    """Metadata describing a single browser cookie (excluding sensitive value unless needed)."""

    name: Optional[str] = None
    domain: Optional[str] = None
    path: Optional[str] = None
    httpOnly: Optional[bool] = None
    secure: Optional[bool] = None
    expires: Optional[float] = None


class CookieSnapshotResponse(BaseModel):
    """Structured response payload for `browser_get_cookies`."""

    url: str
    cookieCount: int
    cookieHeader: str
    userAgent: str
    cookies: list[CookieRecord] = Field(default_factory=list)


class DownloadResultResponse(BaseModel):
    """Structured response payload for `browser_download_url`."""

    ok: bool
    status: int = 0
    statusText: Optional[str] = None
    url: str
    savedPath: Optional[str] = None
    hostPath: Optional[str] = None
    sizeBytes: Optional[int] = None
    sha256: Optional[str] = None
    contentType: Optional[str] = None
    headPreview: Optional[str] = None


class PasteResultResponse(BaseModel):
    """Structured response payload for `browser_paste`."""

    status: str = Field(default="pasted")
    selector: str
    pastedChars: int
    retainedChars: int
    matched: bool
    elapsedMs: float
    message: str


class ScrollResultResponse(BaseModel):
    """Structured response payload for `browser_scroll`."""

    status: str = Field(default="scrolled")
    direction: str
    selector: Optional[str] = None
    scrollX: int = 0
    scrollY: int = 0
    scrollHeight: int = 0
    innerHeight: int = 0


class TabInfo(BaseModel):
    """Metadata describing a single live tab within a browser context."""

    tabId: int
    active: bool
    url: str = ""
    title: str = ""


class TabListResponse(BaseModel):
    """Structured response payload for `browser_list_tabs`."""

    sessionId: str
    activeTabId: Optional[int] = None
    tabCount: int
    tabs: list[TabInfo] = Field(default_factory=list)


class TabActionResponse(BaseModel):
    """Structured response payload for `browser_new_tab`, `browser_select_tab`, and `browser_close_tab`."""

    status: str
    sessionId: str
    tabId: Optional[int] = None
    activeTabId: Optional[int] = None
    active: bool = False
    url: str = ""
    title: str = ""
    tabCount: int = 0


class ForkCreateResponse(BaseModel):
    """Structured response payload for `browser_fork_session`."""

    status: str = Field(default="forked")
    sessionId: str
    fromBrowser: str
    url: str = ""
    parentUrl: str = ""
    cookieCount: int = 0
    originCount: int = 0
    forkDurationMs: float = 0.0
    tabId: int = 0


class ForkPromoteResponse(BaseModel):
    """Structured response payload for `browser_promote_fork`."""

    status: str = Field(default="promoted")
    sessionId: str
    targetBrowser: str
    promotedUrl: str = ""
    cookiesApplied: int = 0
    originsSynced: int = 0
    forkClosed: bool = True
    navigated: bool = False


class ForkDiscardResponse(BaseModel):
    """Structured response payload for `browser_discard_fork`."""

    status: str = Field(default="discarded")
    sessionId: str
    fromBrowser: Optional[str] = None
    openBrowsers: list[str] = Field(default_factory=list)


def slice_bounded_text(content: str, *, max_chars: int, offset: int) -> str:
    """Slice `content` using non-negative `offset` and optional `max_chars` (`<= 0` unlimited)."""
    total_len = len(content)
    start = max(0, offset)
    if start > 0 and start >= total_len:
        return f"(offset {start} is past end of output; totalChars={total_len})"

    if max_chars <= 0:
        chunk = content[start:]
        if start == 0:
            return chunk
        end = total_len
        remaining = 0
    else:
        end = min(total_len, start + max_chars)
        chunk = content[start:end]
        remaining = total_len - end

    if start == 0 and end == total_len:
        return chunk

    return (
        f"{chunk}\n\n[truncated: showing chars {start}..{end} of {total_len} "
        f"({remaining} remaining); pass offset={end} or narrower selector, "
        f"or max_chars=0 for full output]"
    )


def sanitize_cookies(raw_cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip read-only browser cookie fields (e.g. `size`, `session`) rejected by `setCookies`."""
    cleaned: list[dict[str, Any]] = []
    for cookie in raw_cookies:
        name = cookie.get("name")
        domain = cookie.get("domain")
        if not name or not domain:
            continue
        item: dict[str, Any] = {
            "name": name,
            "value": cookie.get("value", ""),
            "domain": domain,
            "path": cookie.get("path") or "/",
        }
        if "httpOnly" in cookie:
            item["httpOnly"] = bool(cookie["httpOnly"])
        if "secure" in cookie:
            item["secure"] = bool(cookie["secure"])
        if cookie.get("sameSite") in ("Strict", "Lax", "None"):
            item["sameSite"] = cookie["sameSite"]
        exp = cookie.get("expires")
        if isinstance(exp, (int, float)) and exp > 0:
            item["expires"] = exp
        cleaned.append(item)
    return cleaned
