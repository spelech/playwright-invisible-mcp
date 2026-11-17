"""Authenticated in-browser cookie inspection and file download services."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .models import CookieRecord, CookieSnapshotResponse, DownloadResultResponse


class PageDownloadService:
    """Provides cookie snapshot inspection and authenticated file downloads inside Firefox."""

    def __init__(
        self,
        *,
        downloads_dir: Path = Path("/data/downloads"),
        host_downloads_prefix: Optional[str] = None,
    ) -> None:
        self._downloads_dir = downloads_dir
        self._downloads_dir.mkdir(parents=True, exist_ok=True)
        resolved_prefix = (
            host_downloads_prefix
            or os.environ.get("STEALTHFOX_HOST_DOWNLOADS_DIR")
            or str(downloads_dir)
        )
        self._host_downloads_prefix = resolved_prefix.rstrip("/")

    async def get_cookies(self, session: Any, url: Optional[str] = None) -> str:
        """Retrieve context cookies (including HttpOnly) and User-Agent as a Pydantic JSON string."""
        page = session.page()
        target_url = url or page.url
        ctx = session._context
        urls = [target_url] if target_url and target_url.startswith("http") else None
        raw_cookies = await ctx.cookies(urls) if urls else await ctx.cookies()
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in raw_cookies if c.get("name"))
        ua = await page.evaluate("navigator.userAgent")
        response = CookieSnapshotResponse(
            url=target_url,
            cookieCount=len(raw_cookies),
            cookieHeader=cookie_header,
            userAgent=str(ua),
            cookies=[
                CookieRecord(
                    name=c.get("name"),
                    domain=c.get("domain"),
                    path=c.get("path"),
                    httpOnly=c.get("httpOnly"),
                    secure=c.get("secure"),
                    expires=c.get("expires"),
                )
                for c in raw_cookies
            ],
        )
        return response.model_dump_json()

    async def _try_native_cad_download(
        self,
        page: Any,
        full_url: str,
        out_path: Path,
    ) -> Optional[DownloadResultResponse]:
        """Attempt a trusted click + native Playwright download on McMaster CAD2 pages."""
        if not hasattr(page, "expect_download") or not hasattr(page, "click"):
            return None

        wants_step = full_url.upper().endswith(".STEP") or out_path.suffix.upper() in (
            ".STEP",
            ".STP",
        )
        cad_state = await page.evaluate(
            """(wantsStep) => {
                const anchor = document.querySelector("a[href*='/Library/CAD2/']");
                const combo = document.querySelector("[aria-label='Select CAD file type']");
                if (!anchor && !combo) return { hasCadControl: false };
                window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
                const href = anchor ? (anchor.getAttribute('href') || '') : '';
                const isStepSelected = href.toUpperCase().endsWith('.STEP');
                return {
                    hasCadControl: true,
                    hasCombo: Boolean(combo),
                    href,
                    needsStepSelect: Boolean(wantsStep && !isStepSelected && combo)
                };
            }""",
            wants_step,
        )
        if not isinstance(cad_state, dict) or not cad_state.get("hasCadControl"):
            return None

        if cad_state.get("needsStepSelect"):
            step_opt_visible = await page.evaluate(
                "Boolean(document.querySelector(\"[id^='dropdown-3-D STEP/']\"))"
            )
            if not step_opt_visible:
                await page.click("[aria-label='Select CAD file type']", timeout=5000)
            await page.click("[id^='dropdown-3-D STEP/']", timeout=5000)

        resolved_href = await page.evaluate(
            """() => {
                const a = document.querySelector("a[href*='/Library/CAD2/']");
                return a ? new URL(a.getAttribute('href') || '', location.origin).href : '';
            }"""
        )
        effective_url = str(resolved_href or full_url)

        async with page.expect_download(timeout=15000) as dl_info:
            await page.click("a[href*='/Library/CAD2/']", timeout=5000)
        download = await dl_info.value
        await download.save_as(str(out_path))

        raw_bytes = out_path.read_bytes()
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
        head_preview = raw_bytes[:120].decode("utf-8", errors="replace").strip()
        ext = out_path.suffix.lstrip(".").upper() or "OCTET-STREAM"

        return DownloadResultResponse(
            ok=True,
            status=200,
            url=effective_url,
            savedPath=str(out_path),
            hostPath=f"{self._host_downloads_prefix}/{out_path.name}",
            sizeBytes=len(raw_bytes),
            sha256=sha256_hex,
            contentType=f"application/{ext}",
            headPreview=head_preview,
        )

    async def download_url(
        self,
        session: Any,
        url: str,
        filename: Optional[str] = None,
    ) -> str:
        """Download a URL inside the active browser context via native download or `fetch(..., {credentials: 'include'})`."""
        page = session.page()
        current_url = page.url or "https://www.mcmaster.com/"
        full_url = urllib.parse.urljoin(current_url, url)

        if not filename:
            parsed_path = urllib.parse.unquote(urllib.parse.urlparse(full_url).path)
            base = os.path.basename(parsed_path) or "download.bin"
            filename = re.sub(r"[^a-zA-Z0-9._ -]", "_", base)

        out_path = (self._downloads_dir / filename).resolve()
        if not str(out_path).startswith(str(self._downloads_dir.resolve())):
            raise ValueError("Invalid filename: must stay within the downloads directory.")

        # Prefer genuine trusted click + native Playwright download on McMaster CAD2 pages
        # so the browser sends top-level navigation Sec-Fetch-* headers instead of CORS fetch.
        if "/Library/CAD2/" in full_url or "mcmaster.com" in full_url:
            try:
                native_res = await self._try_native_cad_download(page, full_url, out_path)
                if native_res is not None:
                    return native_res.model_dump_json(exclude_none=True)
            except Exception:
                pass

        data_info = await page.evaluate(
            """async (targetUrl) => {
                const resp = await fetch(targetUrl, { credentials: 'include' });
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary = '';
                const chunk = 0x8000;
                for (let i = 0; i < bytes.length; i += chunk) {
                    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                }
                return {
                    ok: resp.ok,
                    status: resp.status,
                    statusText: resp.statusText,
                    contentType: resp.headers.get('content-type') || '',
                    byteLength: bytes.length,
                    base64: btoa(binary)
                };
            }""",
            full_url,
        )

        if not data_info.get("ok"):
            return DownloadResultResponse(
                ok=False,
                status=int(data_info.get("status") or 0),
                statusText=data_info.get("statusText"),
                url=full_url,
            ).model_dump_json(exclude_none=True)

        raw_bytes = base64.b64decode(data_info["base64"])
        head_preview = raw_bytes[:120].decode("utf-8", errors="replace").strip()

        # Detect McMaster's 49-to-329 byte stealth HTML comment block ("<!-- ... -->")
        if (
            len(raw_bytes) < 512
            and head_preview.startswith("<!--")
            and head_preview.endswith("-->")
        ):
            return DownloadResultResponse(
                ok=False,
                status=403,
                statusText="Blocked by CAD2 anti-bot comment response (use trusted download click)",
                url=full_url,
            ).model_dump_json(exclude_none=True)

        out_path.write_bytes(raw_bytes)
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

        return DownloadResultResponse(
            ok=True,
            status=int(data_info.get("status") or 200),
            url=full_url,
            savedPath=str(out_path),
            hostPath=f"{self._host_downloads_prefix}/{out_path.name}",
            sizeBytes=len(raw_bytes),
            sha256=sha256_hex,
            contentType=data_info.get("contentType"),
            headPreview=head_preview,
        ).model_dump_json(exclude_none=True)
