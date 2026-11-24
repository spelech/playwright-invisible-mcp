"""Integration tests for FastMCP tool registration, schema adaptation, and ASGI HTTP endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from wrapper_fakes_page import FakeBrowser
from wrapper_fakes_work import FakeStealthSession

from stealth_wrapper.server import build_wrapper_runtime


@pytest.mark.requirement(
    "MCP-05", "FastMCP server_wrapper registers all 32 tools and dispatches through domain services"
)
async def test_server_wrapper_tool_registration_and_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STEALTHFOX_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("STEALTHFOX_DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("STEALTHFOX_TRACE_FILE", str(tmp_path / "traces" / "tool_calls.jsonl"))

    from invisible_playwright_mcp.mcp import server as upstream_server

    runtime = build_wrapper_runtime(start_engine=False)
    try:
        fake_session = FakeStealthSession()
        fake_session.page().body_text = "ROW-" * 200
        fake_session.page().body_html = "<div>" + ("A" * 500) + "</div>"
        upstream_server.work._open["main"] = fake_session
        upstream_server.work._launched["main"] = {
            "seed": 1001878902,
            "proxy": None,
            "profile_dir": str((tmp_path / "profile").resolve()),
        }
        runtime.fork_service._host_browser_factory = lambda **_kw: FakeBrowser()

        tool_map = {t.name: t for t in upstream_server.mcp._tool_manager.list_tools()}
        expected_tools = {
            "browser_open",
            "browser_close",
            "browser_list",
            "browser_status",
            "browser_navigate",
            "browser_read_text",
            "browser_snapshot",
            "browser_read_html",
            "browser_take_screenshot",
            "browser_watch",
            "browser_click",
            "browser_click_at",
            "browser_type",
            "browser_paste",
            "browser_select_option",
            "browser_upload_files",
            "browser_press_key",
            "browser_evaluate",
            "browser_scroll",
            "browser_wait",
            "browser_get_cookies",
            "browser_download_url",
            "browser_new_tab",
            "browser_list_tabs",
            "browser_select_tab",
            "browser_close_tab",
            "browser_fork_session",
            "browser_promote_fork",
            "browser_discard_fork",
            "browser_aac_status",
            "browser_fill_credentials",
            "browser_trace_summary",
        }
        assert expected_tools.issubset(set(tool_map))

        for adapted_name in (
            "browser_click",
            "browser_type",
            "browser_press_key",
            "browser_take_screenshot",
        ):
            validated = tool_map[adapted_name].fn_metadata.arg_model.model_validate(
                {
                    **(
                        {"selector": "#btn"}
                        if adapted_name in ("browser_click", "browser_type")
                        else {}
                    ),
                    **({"text": "hi"} if adapted_name == "browser_type" else {}),
                    **({"key": "Enter"} if adapted_name == "browser_press_key" else {}),
                    "browser": "fork-1",
                    "tab_id": 1,
                }
            )
            assert validated.browser == "fork-1"
            assert validated.tab_id == 1

        status_validated = tool_map["browser_status"].fn_metadata.arg_model.model_validate(
            {"browser": "fork-1"}
        )
        assert status_validated.browser == "fork-1"

        eval_res = await runtime.tools["browser_evaluate"](function="navigator.userAgent")
        assert "Firefox/135.0" in json.loads(eval_res)

        with pytest.raises(ValueError, match="Provide `expression`"):
            await runtime.tools["browser_evaluate"]()

        snap_res = json.loads(
            await runtime.tools["browser_snapshot"](viewport_only=True, max_elements=1)
        )
        assert len(snap_res["interactive_elements"]) == 1

        paste_res = json.loads(
            await runtime.tools["browser_paste"](
                selector="#bom-input",
                text="91251A197, 10\n98164A120, 5",
            )
        )
        assert paste_res["status"] == "pasted"
        assert paste_res["matched"] is True

        monkeypatch.setattr(runtime.action_service, "_orig_read_text", _fake_read_text)
        monkeypatch.setattr(runtime.action_service, "_orig_read_html", _fake_read_html)
        read_txt = await runtime.tools["browser_read_text"](max_chars=120, offset=0)
        assert read_txt.startswith("ROW-" * 30)
        assert "[truncated: showing chars 0..120 of 800" in read_txt

        read_html = await runtime.tools["browser_read_html"](mode="form", max_chars=40, offset=0)
        assert "[truncated: showing chars 0..40 of " in read_html

        tab_created = json.loads(await runtime.tools["browser_new_tab"](activate=False))
        assert tab_created["tabId"] == 1
        assert tab_created["activeTabId"] == 0

        tab_listed = json.loads(await runtime.tools["browser_list_tabs"]())
        assert tab_listed["tabCount"] == 2
        assert tab_listed["activeTabId"] == 0

        tab_selected = json.loads(await runtime.tools["browser_select_tab"](tab_id=1))
        assert tab_selected["activeTabId"] == 1

        tab_closed = json.loads(await runtime.tools["browser_close_tab"](tab_id=1))
        assert tab_closed["status"] == "closed"
        assert tab_closed["tabCount"] == 1

        forked = json.loads(
            await runtime.tools["browser_fork_session"](
                from_browser="main",
                session_id="speculative-check",
            )
        )
        assert forked["status"] == "forked"
        assert forked["sessionId"] == "speculative-check"

        fork_eval = await runtime.tools["browser_evaluate"](
            expression="navigator.userAgent",
            browser="speculative-check",
        )
        assert "Firefox/135.0" in json.loads(fork_eval)

        discarded = json.loads(
            await runtime.tools["browser_discard_fork"](session_id="speculative-check")
        )
        assert discarded["status"] == "discarded"
        assert "speculative-check" not in upstream_server.work._open

        scroll_res = json.loads(
            await runtime.tools["browser_scroll"](direction="down", amount=300, settle_ms=0)
        )
        assert scroll_res["scrollY"] == 300

        wait_res = await runtime.tools["browser_wait"](timeMs=5)
        assert "waited 5ms" in wait_res

        cookies_res = json.loads(await runtime.tools["browser_get_cookies"]())
        assert cookies_res["cookieCount"] == 2

        dl_res = json.loads(
            await runtime.tools["browser_download_url"](
                url="https://www.mcmaster.com/mvC/Library/CAD2/5278N42.STEP",
                filename="test_part.step",
            )
        )
        assert dl_res["ok"] is True

        transport = httpx.ASGITransport(app=runtime.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for endpoint in ("/healthz", "/health"):
                resp = await client.get(endpoint)
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "healthy"
                assert body["service"] == "invisible-playwright-mcp"
    finally:
        upstream_server.work._open.clear()
        upstream_server.work._launched.clear()
        runtime.restore()


async def _fake_read_text(session: FakeStealthSession, selector: str = "body") -> str:
    texts = await session.page().locator(selector).first.all_inner_texts()
    return texts[0] if texts else f"(no element matches {selector!r})"


async def _fake_read_html(session: FakeStealthSession, mode: str = "form") -> str:
    return f"<!-- mode={mode} -->{session.page().body_html}"
