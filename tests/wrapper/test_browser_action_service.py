"""Tests for BrowserActionService and PageDownloadService domain behaviors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from wrapper_fakes_page import FakeStealthPage
from wrapper_fakes_work import FakeStealthSession

from stealth_wrapper.browser_action_service import BrowserActionService
from stealth_wrapper.models import (
    CookieSnapshotResponse,
    DownloadResultResponse,
    PasteResultResponse,
    ScrollResultResponse,
)


class FakeUpstreamActions:
    """In-memory stand-in for `invisible_playwright_mcp.mcp.actions` preserving real refusal rules."""

    SNAPSHOT_JS = "() => ({})"
    ACTION_TIMEOUT_MS = 5000

    @staticmethod
    def _refuse_script_interaction(expression: str) -> None:
        for forbidden in (".click(", ".value =", ".dispatchEvent(", ".submit("):
            if forbidden in expression:
                raise ValueError(f"refused: untrusted script interaction ({forbidden})")

    @staticmethod
    async def snapshot(session: FakeStealthSession) -> str:
        d = await session.page().evaluate_read("SNAPSHOT_JS")
        return json.dumps(d)

    @staticmethod
    async def read_text(session: FakeStealthSession, selector: str = "body") -> str:
        texts = await session.page().locator(selector).first.all_inner_texts()
        if not texts:
            return f"(no element matches {selector!r})"
        return texts[0]

    @staticmethod
    async def read_html(session: FakeStealthSession, mode: str = "form") -> str:
        return f"<!-- mode={mode} -->{session.page().body_html}"

    @staticmethod
    async def _field_state(page: FakeStealthPage, selector: str) -> dict[str, Any]:
        return {
            "value": page.field_values.get(selector, ""),
            "focused": page._focused_selector == selector,
            "secret": False,
        }

    @staticmethod
    def what_the_field_kept(
        selector: str, text: str, before: dict[str, Any], after: dict[str, Any]
    ) -> str:
        del before
        val = after.get("value")
        if val == text:
            return f"typed into {selector}"
        return f"typed into {selector}; the page shows it as {val!r}"

    @staticmethod
    async def _resolve_nth(_page: FakeStealthPage, elements: list[dict[str, Any]]) -> None:
        for idx, elem in enumerate(elements, start=1):
            if elem.get("ambiguous"):
                elem["selector"] = f":nth-match({elem['selector']}, {idx})"


@pytest.mark.requirement(
    "SEC-01",
    "browser_evaluate blocks untrusted DOM mutations while permitting read and async expressions",
)
async def test_evaluate_enforces_stealth_guardrails_and_runs_expressions(
    tmp_path: Path,
) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    service.install()
    session = FakeStealthSession()

    with pytest.raises(ValueError, match="non-empty"):
        await service.evaluate_expression(session, "   ")

    with pytest.raises(ValueError, match="refused"):
        await service.evaluate_expression(session, "document.querySelector('#btn').click()")
    with pytest.raises(ValueError, match="refused"):
        await service.evaluate_expression(session, "el.value = 'injected'")

    res_json = await service.evaluate_expression(session, "navigator.userAgent")
    assert "Firefox/135.0" in json.loads(res_json)


@pytest.mark.requirement(
    "MCP-01", "Scoped browser_snapshot filters by CSS bounding box, viewport_only, and max_elements"
)
async def test_scoped_snapshot_filters_and_caps_elements(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    service.install()
    session = FakeStealthSession()

    unfiltered = json.loads(await service.scoped_snapshot(session))
    assert len(unfiltered["interactive_elements"]) == 3

    viewport_only = json.loads(await service.scoped_snapshot(session, viewport_only=True))
    assert len(viewport_only["interactive_elements"]) == 2
    assert all(not e.get("off_screen") for e in viewport_only["interactive_elements"])

    scoped = json.loads(
        await service.scoped_snapshot(
            session,
            selector="#main-catalog",
            viewport_only=True,
            max_elements=1,
        )
    )
    assert scoped["truncated_from"] == 2
    assert len(scoped["interactive_elements"]) == 1
    assert scoped["interactive_elements"][0]["selector"] == "a[href='/part-1']"


@pytest.mark.requirement(
    "MCP-02",
    "browser_scroll supports directional mouse wheel, fallback scrollBy, and selector scrollIntoView",
)
async def test_scroll_page_directions_fallback_and_selector(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    session = FakeStealthSession()

    down_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, direction="down", amount=500, settle_ms=0)
    )
    assert down_res.scrollY == 500

    session.page().mouse.fail_wheel = True
    up_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, direction="up", amount=200, settle_ms=0)
    )
    assert up_res.scrollY == 300

    right_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, direction="right", amount=150, settle_ms=0)
    )
    assert right_res.scrollX == 150

    left_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, direction="left", amount=50, settle_ms=0)
    )
    assert left_res.scrollX == 100

    bottom_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, direction="bottom", settle_ms=0)
    )
    assert bottom_res.scrollY == 3200

    top_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, direction="top", settle_ms=0)
    )
    assert top_res.scrollY == 0

    sel_res = ScrollResultResponse.model_validate_json(
        await service.scroll_page(session, selector="#empty-box", settle_ms=0)
    )
    assert sel_res.scrollY == 2000

    missing_sel = await service.scroll_page(session, selector="#nonexistent", settle_ms=0)
    assert "no element matches" in missing_sel


@pytest.mark.requirement(
    "MCP-03",
    "browser_wait and browser_get_cookies return bounded waits and full HttpOnly cookie snapshots",
)
async def test_wait_and_get_cookies(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    session = FakeStealthSession()

    wait_time = await service.wait_for_page(session, time_ms=10)
    assert "waited 10ms" in wait_time

    wait_sel = await service.wait_for_page(session, time_ms=1200, selector="#spec-table")
    assert "selector '#spec-table' is present" in wait_sel
    assert session.page().waited_selectors == [("#spec-table", 1200)]

    cookie_snap = CookieSnapshotResponse.model_validate_json(await service.get_cookies(session))
    assert cookie_snap.cookieCount == 2
    assert "StayLoggedIn=true" in cookie_snap.cookieHeader
    assert "ak_bmsc=stealth-akamai-token" in cookie_snap.cookieHeader
    assert any(c.name == "ak_bmsc" and c.httpOnly is True for c in cookie_snap.cookies)


@pytest.mark.requirement(
    "MCP-04",
    "browser_download_url saves verified STEP files to disk and rejects path traversal or HTTP 403",
)
async def test_download_url_saves_step_and_handles_errors(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    session = FakeStealthSession()

    dl_ok = DownloadResultResponse.model_validate_json(
        await service.download_url(
            session,
            url="/mvC/Library/CAD2/5278N42.STEP",
            filename="5278N42.step",
        )
    )
    assert dl_ok.ok is True
    assert dl_ok.status == 200
    assert dl_ok.sizeBytes and dl_ok.sizeBytes > 50
    assert dl_ok.headPreview and dl_ok.headPreview.startswith("ISO-10303-21;")
    saved_file = Path(dl_ok.savedPath or "")
    assert saved_file.exists()
    assert saved_file.read_bytes().startswith(b"ISO-10303-21;")

    with pytest.raises(ValueError, match="Invalid filename"):
        await service.download_url(
            session,
            url="/mvC/Library/CAD2/5278N42.STEP",
            filename="../../escape.step",
        )

    dl_403 = DownloadResultResponse.model_validate_json(
        await service.download_url(
            session,
            url="https://www.mcmaster.com/mvC/Library/CAD2/missing.STEP",
        )
    )
    assert dl_403.ok is False
    assert dl_403.status == 403

    dl_comment = DownloadResultResponse.model_validate_json(
        await service.download_url(
            session,
            url="https://www.mcmaster.com/mvD/Library/CAD2/97355A439.STEP",
        )
    )
    assert dl_comment.ok is False
    assert dl_comment.status == 403

    session.page().cad_control_enabled = True
    dl_native = DownloadResultResponse.model_validate_json(
        await service.download_url(
            session,
            url="https://www.mcmaster.com/mvD/Library/CAD2/97355A439.STEP",
            filename="97355A439.step",
        )
    )
    assert dl_native.ok is True
    assert dl_native.status == 200
    assert dl_native.headPreview and dl_native.headPreview.startswith("ISO-10303-21;")
    assert session.page().clicked_selectors == [
        "[aria-label='Select CAD file type']",
        "[id^='dropdown-3-D STEP/']",
        "a[href*='/Library/CAD2/']",
    ]


@pytest.mark.requirement(
    "MCP-06", "browser_paste copies text to browser clipboard and sends trusted Control+v events"
)
async def test_paste_text_trusted_clipboard_and_clear_modes(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    service.install()
    session = FakeStealthSession()
    bom_payload = "91251A197, 25\n98164A120, 10\n90576A115, 50"

    res_clear = PasteResultResponse.model_validate_json(
        await service.paste_text(session, "#bom-input", bom_payload, clear=True)
    )
    assert res_clear.status == "pasted"
    assert res_clear.matched is True
    assert res_clear.pastedChars == len(bom_payload)
    assert res_clear.retainedChars == len(bom_payload)
    assert res_clear.message == "pasted into #bom-input"
    assert session.page().field_values["#bom-input"] == bom_payload
    assert session.page().keyboard.pressed_keys == ["Control+a", "Control+v"]
    assert session.page().events == [
        {"type": "paste", "isTrusted": True, "len": len(bom_payload)},
        {"type": "input", "isTrusted": True, "inputType": "insertFromPaste"},
    ]

    res_append = PasteResultResponse.model_validate_json(
        await service.paste_text(session, "#bom-input", "\n92196A148, 4", clear=False)
    )
    assert res_append.matched is True
    assert session.page().field_values["#bom-input"] == bom_payload + "\n92196A148, 4"

    res_empty = PasteResultResponse.model_validate_json(
        await service.paste_text(session, "#bom-input", "", clear=True)
    )
    assert res_empty.matched is True
    assert session.page().field_values["#bom-input"] == ""


@pytest.mark.requirement(
    "MCP-07",
    "browser_read_text and browser_read_html enforce bounded max_chars with offset pagination",
)
async def test_bounded_read_text_and_read_html_pagination(tmp_path: Path) -> None:
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()
    service = BrowserActionService(FakeUpstreamActions, downloads_dir=downloads_dir)
    service.install()
    session = FakeStealthSession()
    long_catalog = "PART-" * 70
    session.page().body_text = long_catalog
    session.page().body_html = "<div>" + ("X" * 300) + "</div>"

    missing = await service.read_text_bounded(session, selector="#missing", max_chars=100)
    assert missing == "(no element matches '#missing')"

    page_0 = await service.read_text_bounded(session, max_chars=100, offset=0)
    assert page_0.startswith(long_catalog[:100])
    assert "[truncated: showing chars 0..100 of 350 (250 remaining); pass offset=100" in page_0

    page_1 = await service.read_text_bounded(session, max_chars=250, offset=100)
    assert page_1.startswith(long_catalog[100:])
    assert "showing chars 100..350 of 350 (0 remaining)" in page_1

    past_eof = await service.read_text_bounded(session, max_chars=100, offset=999)
    assert "offset 999 is past end of output" in past_eof

    full = await service.read_text_bounded(session, max_chars=0, offset=0)
    assert full == long_catalog

    html_cap = await service.read_html_bounded(session, mode="form", max_chars=50, offset=0)
    assert html_cap.startswith("<!-- mode=form --><div>")
    assert "[truncated: showing chars 0..50 of " in html_cap
    html_full = await service.read_html_bounded(session, mode="full", max_chars=0)
    assert html_full == f"<!-- mode=full -->{session.page().body_html}"
