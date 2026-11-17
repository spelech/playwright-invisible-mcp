"""Unit and integration tests for ToolTraceRecorder, browser_trace_summary, and GET /traces."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from wrapper_fakes_work import FakeStealthSession

from stealth_wrapper.server import build_wrapper_runtime
from stealth_wrapper.tool_trace_recorder import ToolTraceRecorder, sanitize_trace_args


@pytest.mark.requirement(
    "MCP-10",
    "ToolTraceRecorder captures per-call telemetry, redacts secrets, detects multi-step patterns, and exposes /traces",
)
async def test_tool_trace_recorder_patterns_streamlining_and_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_path = tmp_path / "traces" / "tool_calls.jsonl"
    monkeypatch.setenv("STEALTHFOX_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("STEALTHFOX_DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("STEALTHFOX_TRACE_FILE", str(trace_path))
    monkeypatch.setenv("STEALTHFOX_TRACE_MAX_EVENTS", "50")

    sanitized = sanitize_trace_args(
        {
            "api_key": "sk-secret-value",
            "selector": "#key-button",
            "text": "X" * 200,
            "items": list(range(15)),
        },
        max_str_len=20,
    )
    assert sanitized["api_key"] == "<redacted>"
    assert sanitized["selector"] == "#key-button"
    assert sanitized["text"].endswith("...<200 chars>")
    assert sanitized["items"] == "<list len=15>"

    from invisible_playwright_mcp.mcp import server as upstream_server

    runtime = build_wrapper_runtime(start_engine=False)
    try:
        fake_session = FakeStealthSession()
        upstream_server.work._open["main"] = fake_session
        upstream_server.work._launched["main"] = {
            "seed": 1001878902,
            "proxy": None,
            "profile_dir": str((tmp_path / "profile").resolve()),
        }

        for idx in range(2):
            fake_session.page().url = f"https://www.mcmaster.com/parts-{idx}"
            await runtime.tools["browser_scroll"](direction="down", amount=300, settle_ms=0)
            await runtime.tools["browser_scroll"](direction="down", amount=300, settle_ms=0)
            await runtime.tools["browser_snapshot"](viewport_only=True, max_elements=2)
            await runtime.tools["browser_paste"](
                selector="#bulk-lines-textarea",
                text="91251A197, 10\n" * 20,
            )
            await runtime.tools["browser_click"](selector="#add-to-order")

        with pytest.raises(ValueError, match="Provide `expression`"):
            await runtime.tools["browser_evaluate"]()

        assert trace_path.exists()
        lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 11
        assert lines[-1]["status"] == "error"
        assert "ValueError" in lines[-1]["error"]

        paste_events = [e for e in lines if e["tool"] == "browser_paste"]
        assert len(paste_events) == 2
        assert "...<280 chars>" in paste_events[0]["args"]["text"]

        mcp_summary = json.loads(
            await runtime.tools["browser_trace_summary"](
                limit_recent=5,
                min_sequence_count=2,
                reset=False,
            )
        )
        assert mcp_summary["totalCalls"] == 11
        assert mcp_summary["totalErrors"] == 1
        assert len(mcp_summary["recentEvents"]) == 5

        bigram_names = {b["pattern"] for b in mcp_summary["topBigrams"]}
        assert "browser_scroll -> browser_scroll" in bigram_names
        assert "browser_scroll -> browser_snapshot" in bigram_names
        assert "browser_paste -> browser_click" in bigram_names

        trigram_names = {t["pattern"] for t in mcp_summary["topTrigrams"]}
        assert "browser_scroll -> browser_scroll -> browser_snapshot" in trigram_names

        categories = {o["category"] for o in mcp_summary["streamliningOpportunities"]}
        assert "repeated_step_loop" in categories
        assert "action_then_read" in categories
        assert "input_then_submit" in categories
        assert "multi_step_action_chain" in categories

        transport = httpx.ASGITransport(app=runtime.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health_resp = await client.get("/healthz")
            assert health_resp.status_code == 200
            assert health_resp.json()["tracedCalls"] == 11

            traces_resp = await client.get("/traces?limit=3&min_count=2&reset=1")
            assert traces_resp.status_code == 200
            traces_body = traces_resp.json()
            assert traces_body["totalCalls"] == 11
            assert len(traces_body["recentEvents"]) == 3

            after_reset = await client.get("/traces?limit=5&min_count=1")
            assert after_reset.json()["totalCalls"] == 0

        standalone = ToolTraceRecorder(upstream_server.work, enabled=False)
        async def _noop() -> str:
            return "ok"
        wrapped_noop = standalone.wrap_tool("browser_wait", _noop)
        assert standalone.wrap_tool("browser_wait", wrapped_noop) is wrapped_noop
        assert await wrapped_noop() == "ok"
        assert standalone.summarize().totalCalls == 0
    finally:
        upstream_server.work._open.clear()
        upstream_server.work._launched.clear()
        runtime.restore()
