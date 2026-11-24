"""Unit and integration tests for CredentialAccessService, MCP prompts, and zero-secret guarantees."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from wrapper_fakes_work import FakeStealthSession

from stealth_wrapper.credential_access_service import (
    CredentialAccessService,
    extract_bare_domain,
    parse_remote_sessions,
)
from stealth_wrapper.server import build_wrapper_runtime

SAMPLE_CONNECTIONS_OUTPUT = """── Remote Client (ap-cli connect) ──
  Your identity: ac1b525ee58d4a02806b4164ac4a9a02b7e387aaa8188e6384f7ffbcc2a31384
  Connections: (1 peer)
    - e532bef8745b941a2da0879a43b5e3523307cfbf532c48d7886484d848f6a271
      Paired: 1 week ago  |  Last used: 1 week ago

── User Client (ap-cli listen) ──
  Your identity: cecdf2372fb5892c0a1e1cd9357c2cd9f7528f4da09a32b35e18119c11e3c64b
  Connections: (none)
"""


class _RecordingActions:
    """Minimal action double recording real keyboard/click calls made during credential fill."""

    def __init__(self) -> None:
        self.typed: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.pressed: list[str] = []

    async def type_text(self, session: Any, selector: str, text: str) -> str:
        session.page().field_values[selector] = text
        self.typed.append((selector, text))
        return f"typed into {selector}"

    async def click(self, session: Any, selector: str) -> str:
        self.clicked.append(selector)
        session.page().url = "https://example.com/dashboard"
        return f"clicked {selector}"

    async def press_key(self, session: Any, key: str) -> str:
        self.pressed.append(key)
        session.page().url = "https://example.com/dashboard"
        return f"pressed {key}"


def _make_fake_aac_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake_bin = bin_dir / "aac"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)
    return fake_bin


@pytest.mark.requirement(
    "MCP-11",
    "Wrapper registers MCP prompts, resources, and server instructions addendum and restores cleanly",
)
async def test_wrapper_registers_mcp_prompts_resources_and_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STEALTHFOX_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("STEALTHFOX_DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("STEALTHFOX_TRACE_FILE", str(tmp_path / "traces" / "tool_calls.jsonl"))
    monkeypatch.setenv("STEALTHFOX_AAC_HOME", str(tmp_path / "aac"))

    from invisible_playwright_mcp.mcp import server as upstream_server

    orig_instructions = upstream_server.mcp._mcp_server.instructions
    runtime = build_wrapper_runtime(start_engine=False)
    try:
        assert "browser_fill_credentials" in (upstream_server.mcp._mcp_server.instructions or "")

        prompts = await upstream_server.mcp.list_prompts()
        prompt_names = {p.name for p in prompts}
        assert {
            "stealth_browser_workflow",
            "authenticated_login_workflow",
            "multi_step_2fa_login",
        }.issubset(prompt_names)

        login_prompt = await upstream_server.mcp.get_prompt(
            "authenticated_login_workflow",
            {"site_url": "https://github.com/login", "domain": "github.com"},
        )
        rendered_login = " ".join(str(m.content) for m in login_prompt.messages)
        assert "browser_fill_credentials" in rendered_login
        assert 'domain="github.com"' in rendered_login

        totp_prompt = await upstream_server.mcp.get_prompt(
            "multi_step_2fa_login",
            {"domain": "github.com"},
        )
        rendered_totp = " ".join(str(m.content) for m in totp_prompt.messages)
        assert "Approve 10m" in rendered_totp
        assert "fill_totp=True" in rendered_totp

        resources = await upstream_server.mcp.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        assert "stealth://guide/overview" in resource_uris
        assert "stealth://guide/agent-access" in resource_uris

        guide_chunks = await upstream_server.mcp.read_resource("stealth://guide/agent-access")
        guide_text = " ".join(getattr(c, "content", str(c)) for c in guide_chunks)
        assert "Vaultwarden" in guide_text
        assert "ntfy" in guide_text
    finally:
        runtime.restore()

    assert upstream_server.mcp._mcp_server.instructions == orig_instructions
    restored_prompts = await upstream_server.mcp.list_prompts()
    assert "authenticated_login_workflow" not in {p.name for p in restored_prompts}


@pytest.mark.requirement(
    "MCP-12",
    "CredentialAccessService fetches human-approved credentials via aac and injects them without leaking secrets",
)
async def test_credential_access_injects_secrets_without_leaking_to_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert extract_bare_domain("https://www.McMaster.com:443/login") == "mcmaster.com"
    assert extract_bare_domain("accounts.google.com/v3/signin") == "accounts.google.com"
    assert parse_remote_sessions(SAMPLE_CONNECTIONS_OUTPUT) == [
        "e532bef8745b941a2da0879a43b5e3523307cfbf532c48d7886484d848f6a271"
    ]

    fake_bin = _make_fake_aac_bin(tmp_path)
    secret_pw = "SuperS3cret-Password-991!"
    secret_totp = "748291"
    seen_cmds: list[list[str]] = []

    async def fake_runner(
        cmd: list[str],
        env: dict[str, str],
        timeout_s: float,
    ) -> tuple[int, str, str]:
        del timeout_s
        seen_cmds.append(cmd)
        assert env.get("HOME") == str(tmp_path / "aac")
        if cmd[1:3] == ["connections", "list"]:
            return 0, SAMPLE_CONNECTIONS_OUTPUT, ""
        return (
            0,
            json.dumps(
                {
                    "success": True,
                    "domain": "example.com",
                    "credential": {
                        "username": "alice@example.com",
                        "password": secret_pw,
                        "totp": secret_totp,
                    },
                }
            ),
            "",
        )

    actions_double = _RecordingActions()
    service = CredentialAccessService(
        actions_module=actions_double,
        enabled=True,
        aac_bin=str(fake_bin),
        aac_home=tmp_path / "aac",
        command_runner=fake_runner,
    )

    session = FakeStealthSession()
    session.page().url = "https://www.example.com/signin"

    async def fake_evaluate(script: str, *_args: Any) -> Any:
        if "one-time-code" in script:
            return {
                "usernameSelector": "#email",
                "passwordSelector": "#password",
                "totpSelector": "#otp-code",
                "submitSelector": "#sign-in-btn",
            }
        return None

    monkeypatch.setattr(session.page(), "evaluate", fake_evaluate)

    status_payload = json.loads(await service.check_status(session=session))
    assert status_payload["enabled"] is True
    assert status_payload["paired"] is True
    assert status_payload["detectedDomain"] == "example.com"
    assert status_payload["detectedFields"]["passwordSelector"] == "#password"

    fill_raw = await service.fill_credentials(
        session,
        fill_username=True,
        fill_password=True,
        fill_totp=True,
        submit=True,
    )
    assert secret_pw not in fill_raw
    assert secret_totp not in fill_raw
    assert "alice@example.com" not in fill_raw

    fill_data = json.loads(fill_raw)
    assert fill_data["status"] == "filled"
    assert fill_data["domain"] == "example.com"
    assert fill_data["usernameFilled"] is True
    assert fill_data["passwordFilled"] is True
    assert fill_data["totpFilled"] is True
    assert fill_data["submitted"] is True
    assert fill_data["urlChanged"] is True
    assert fill_data["availableFields"] == ["username", "password", "totp"]
    assert session.page().field_values["#email"] == "alice@example.com"
    assert session.page().field_values["#password"] == secret_pw
    assert session.page().field_values["#otp-code"] == secret_totp
    assert actions_double.clicked == ["#sign-in-btn"]
    assert any(
        "--domain" in cmd and cmd[cmd.index("--domain") + 1] == "example.com"
        for cmd in seen_cmds
    )


@pytest.mark.requirement(
    "SEC-03",
    "CredentialAccessService handles denials, timeouts, fingerprint mismatches, and keeps trace logs secret-free",
)
async def test_credential_access_error_paths_and_zero_secret_traces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = _make_fake_aac_bin(tmp_path)
    trace_path = tmp_path / "traces" / "tool_calls.jsonl"
    monkeypatch.setenv("STEALTHFOX_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setenv("STEALTHFOX_DOWNLOADS_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("STEALTHFOX_TRACE_FILE", str(trace_path))
    monkeypatch.setenv("STEALTHFOX_AAC_BIN", str(fake_bin))
    monkeypatch.setenv("STEALTHFOX_AAC_HOME", str(tmp_path / "aac"))

    from invisible_playwright_mcp.mcp import server as upstream_server

    runtime = build_wrapper_runtime(start_engine=False)
    try:
        session = FakeStealthSession()
        session.page().url = "https://github.com/login"
        upstream_server.work._open["main"] = session
        upstream_server.work._launched["main"] = {
            "seed": 1001878902,
            "proxy": None,
            "profile_dir": str((tmp_path / "profile").resolve()),
        }

        # 1. No visible fields -> returns no_fields WITHOUT calling aac
        called_aac = False

        async def should_not_run(*_args: Any) -> tuple[int, str, str]:
            nonlocal called_aac
            called_aac = True
            return 0, "", ""

        runtime.credential_service._runner = should_not_run
        no_field_resp = json.loads(
            await runtime.tools["browser_fill_credentials"](domain="github.com")
        )
        assert no_field_resp["status"] == "no_fields"
        assert called_aac is False

        # 2. Exit code error mapping (denied, not_found, fingerprint_mismatch, timeout)
        async def _check_case(rc: int, stderr: str, expected_status: str) -> None:
            async def _runner(cmd: list[str], _env: dict[str, str], _t: float) -> tuple[int, str, str]:
                if cmd[1:3] == ["connections", "list"]:
                    return 0, SAMPLE_CONNECTIONS_OUTPUT, ""
                return rc, "", stderr

            runtime.credential_service._runner = _runner
            resp = json.loads(
                await runtime.tools["browser_fill_credentials"](
                    domain="github.com",
                    username_selector="#login_field",
                    password_selector="#password",
                )
            )
            assert resp["status"] == expected_status

        await _check_case(1, "Request denied by user", "denied")
        await _check_case(4, "No item found", "not_found")
        await _check_case(5, "Fingerprint mismatch", "fingerprint_mismatch")
        await _check_case(2, "Relay unreachable", "connection_failed")
        await _check_case(3, "Handshake failed", "auth_failed")

        async def _timeout_runner(cmd: list[str], _env: dict[str, str], _t: float) -> tuple[int, str, str]:
            if cmd[1:3] == ["connections", "list"]:
                return 0, SAMPLE_CONNECTIONS_OUTPUT, ""
            raise asyncio.TimeoutError()

        runtime.credential_service._runner = _timeout_runner
        timeout_resp = json.loads(
            await runtime.tools["browser_fill_credentials"](
                domain="github.com",
                username_selector="#login_field",
            )
        )
        assert timeout_resp["status"] == "timeout"

        # 3. Verify trace file recorded tool calls without any secret tokens
        assert trace_path.exists()
        trace_raw = trace_path.read_text(encoding="utf-8")
        assert "browser_fill_credentials" in trace_raw
        assert "AAC_TOKEN" not in trace_raw
    finally:
        upstream_server.work._open.clear()
        upstream_server.work._launched.clear()
        runtime.restore()
