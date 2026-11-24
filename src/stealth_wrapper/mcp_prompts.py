"""MCP prompts, resources, and server-instruction addendum for agents without local skills."""

from __future__ import annotations

from typing import Any

WRAPPER_INSTRUCTIONS_ADDENDUM = """

---
Enhanced Stealth Wrapper Capabilities:
- Multi-tab & Parallel Forks: Use `browser_new_tab`, `browser_list_tabs`, `browser_select_tab`, and `browser_close_tab` (or pass `tab_id` to any page tool) to work across multiple tabs in the same authenticated session. Use `browser_fork_session` / `browser_promote_fork` / `browser_discard_fork` for isolated parallel browsing.
- Scoped Reads & Clipboard Paste: Prefer `browser_snapshot(viewport_only=True, max_elements=60)` and `browser_scroll` on large pages. Use `browser_paste` for multi-line or bulk text entry via genuine `Control+v`.
- Human-Gated Vaultwarden Login (`aac`): NEVER ask the user to type or paste passwords into chat when Agent Access Control is available. Call `browser_aac_status` to check readiness, navigate to the site's login form, and call `browser_fill_credentials` so the server requests a human-approved Vaultwarden credential (via ntfy push on the user's phone) and types it directly into the page without exposing secrets in the conversation. See the `authenticated_login_workflow` and `multi_step_2fa_login` MCP prompts or `stealth://guide/agent-access` resource for step-by-step instructions.
"""

OVERVIEW_RESOURCE_MD = """# Stealth Playwright MCP Server Guide

This server drives an anti-detect Firefox browser (`main` persistent profile + optional `support` helper + isolated `browser_fork_session` contexts).

## Core Principles
1. **Use real input tools only**: `browser_click`, `browser_type`, `browser_paste`, `browser_select_option`, `browser_press_key`, `browser_scroll`. Never mutate form values with `browser_evaluate`.
2. **Keep snapshots small**: Pass `viewport_only=True` and `max_elements=60` (or a `selector`) to `browser_snapshot`. Use `max_chars` / `offset` on `browser_read_text` and `browser_read_html`.
3. **Preserve authenticated sessions**: `main` persists cookies and logins across turns. Use `browser_new_tab` + `tab_id` for concurrent tabs, or `browser_fork_session` for speculative parallel checks.
4. **Human-gated logins**: Use `browser_aac_status` and `browser_fill_credentials` to sign into websites using Vaultwarden Agent Access Control without secrets ever entering the chat transcript.
5. **Telemetry**: Call `browser_trace_summary` (or `GET /traces`) to inspect tool latency and multi-step streamlining opportunities.
"""

AGENT_ACCESS_RESOURCE_MD = """# Vaultwarden Agent Access Control (`aac`) Login Guide

This MCP server integrates with Bitwarden/Vaultwarden **Agent Access Control (`aac`)** and **ntfy** push notifications so any agent — even without local CLI tools or skills — can sign into websites safely.

## Security Model
- Secrets are fetched inside the MCP server container over an end-to-end encrypted tunnel (`wss://ap.lesspassword.dev`) from the user's paired `aac listen` host.
- Every release requires the user to tap **Approve** or **Approve 10m** on an ntfy push notification on their phone.
- Credentials are typed directly into the browser form via genuine keyboard events (`isTrusted: true`) and **never** returned in tool output or written to trace logs.

## Standard Login Flow
1. Call `browser_aac_status` to verify `enabled: true` and `paired: true` (or `hasBootstrapToken: true`).
2. Navigate to the target login page with `browser_navigate`.
3. Call `browser_fill_credentials(submit=True)` (optionally passing `domain`, `item_id`, or explicit CSS selectors if auto-detection misses a custom widget).
4. Wait while the user approves the ntfy push (default timeout 120s).
5. Inspect `urlChanged` and `browser_snapshot` to confirm sign-in or detect a 2FA / second-stage prompt.

## Multi-Stage & 2FA Logins
Many sites split login into **Step 1 (Email/Username)** -> **Step 2 (Password)** -> **Step 3 (TOTP 2FA)**:
- Step 1: Call `browser_fill_credentials(fill_username=True, fill_password=False, submit=True)`.
- Step 2: After the password field appears, call `browser_fill_credentials(fill_username=False, fill_password=True, submit=True)`.
- Step 3 (if 2FA is requested): Call `browser_fill_credentials(fill_username=False, fill_password=False, fill_totp=True, submit=True)`.
- Tip: Tell the user they can tap **Approve 10m** on the first ntfy push so Steps 2 and 3 auto-approve cleanly.

## Handling Errors
- `timeout`: Tell the user an approval notification is waiting on their phone and retry once they tap Approve.
- `denied`: Respect the denial immediately and ask the user how to proceed.
- `not_found`: Confirm the bare domain (e.g., `github.com`, not `https://github.com/login`) or ask for the Vaultwarden `item_id`.
- `auth_failed` / `connection_failed`: The `aac` listener may be offline or needs re-pairing.
- `fingerprint_mismatch`: **STOP immediately** and alert the user to a potential security issue.
"""


def register_wrapper_prompts(mcp: Any) -> dict[str, Any]:
    """Register MCP prompts, resources, and server instructions addendum on `mcp`."""
    orig_instructions = getattr(mcp._mcp_server, "instructions", None)
    if isinstance(orig_instructions, str) and WRAPPER_INSTRUCTIONS_ADDENDUM.strip() not in orig_instructions:
        mcp._mcp_server.instructions = orig_instructions + WRAPPER_INSTRUCTIONS_ADDENDUM

    @mcp.prompt(
        name="stealth_browser_workflow",
        description="Core workflow for driving this stealth Playwright MCP server (tabs, forks, snapshots, paste, and logins).",
    )
    def stealth_browser_workflow(task: str = "") -> str:
        header = f"Task: {task}\n\n" if task.strip() else ""
        return (
            f"{header}"
            "Follow these rules when driving the Stealth Playwright MCP browser:\n"
            "1. Call `browser_navigate(url)` directly (the wrapper auto-opens `main` if needed) and prefer `browser_snapshot(viewport_only=True, max_elements=60)`.\n"
            "2. Use `browser_scroll` to load lazy content, `browser_paste` for multi-line inputs, and `browser_click` / `browser_type` for all interactions (never mutate values via `browser_evaluate`).\n"
            "3. Use `browser_new_tab` + `tab_id` for multi-tab work, or `browser_fork_session` for parallel isolated branches.\n"
            "4. If the site requires authentication, call `browser_aac_status` and `browser_fill_credentials` instead of asking the user for passwords in chat.\n"
        )

    @mcp.prompt(
        name="authenticated_login_workflow",
        description="Sign into a website using Vaultwarden Agent Access Control (aac) + ntfy human approval without exposing secrets.",
    )
    def authenticated_login_workflow(site_url: str = "", domain: str = "") -> str:
        target = site_url or (f"https://{domain}" if domain else "<target login URL>")
        domain_hint = f'domain="{domain}", ' if domain else ""
        return (
            f"Sign into {target} using the server's built-in Vaultwarden Agent Access Control (`aac`) pathway:\n"
            "1. Call `browser_aac_status()` to confirm `aac` is enabled and paired.\n"
            f"2. Navigate to {target} with `browser_navigate` and inspect the login form with `browser_snapshot(viewport_only=True)`.\n"
            f"3. Call `browser_fill_credentials({domain_hint}submit=True)`. This sends an ntfy push notification to the user's phone "
            "and waits up to 120s for them to tap **Approve** or **Approve 10m**, then types the credentials directly into the page without exposing secrets in chat.\n"
            "4. If the site uses a multi-step flow (username -> Next -> password -> 2FA), call `browser_fill_credentials` at each stage "
            "with the appropriate `fill_username`, `fill_password`, or `fill_totp` flags.\n"
            "5. If `status` is `timeout`, tell the user the approval push is waiting on their phone. If `status` is `denied` or `fingerprint_mismatch`, stop and inform the user.\n"
        )

    @mcp.prompt(
        name="multi_step_2fa_login",
        description="Handle multi-stage email -> password -> TOTP 2FA logins using Agent Access Control (`Approve 10m`).",
    )
    def multi_step_2fa_login(domain: str = "") -> str:
        dom_arg = f'domain="{domain}", ' if domain else ""
        return (
            f"Multi-step / 2FA login playbook{f' for {domain}' if domain else ''}:\n"
            "Note: Let the user know they can tap **Approve 10m** on the first ntfy push so subsequent password and TOTP steps complete without extra prompts.\n"
            f"1. Email/Username stage: `browser_fill_credentials({dom_arg}fill_username=True, fill_password=False, submit=True)`.\n"
            "2. Confirm the password input appeared via `browser_snapshot(viewport_only=True)`.\n"
            f"3. Password stage: `browser_fill_credentials({dom_arg}fill_username=False, fill_password=True, submit=True)`.\n"
            "4. If a 2FA / TOTP code input appears, call:\n"
            f"   `browser_fill_credentials({dom_arg}fill_username=False, fill_password=False, fill_totp=True, submit=True)`.\n"
            "5. Verify the authenticated landing page via `browser_snapshot(viewport_only=True)`.\n"
        )

    @mcp.resource(
        "stealth://guide/overview",
        name="Stealth Browser Overview",
        description="Overview of stealth Playwright MCP tools, tabs, forks, and best practices.",
        mime_type="text/markdown",
    )
    def overview_resource() -> str:
        return OVERVIEW_RESOURCE_MD

    @mcp.resource(
        "stealth://guide/agent-access",
        name="Agent Access Control Login Guide",
        description="Guide to human-approved Vaultwarden/Bitwarden logins via aac and ntfy.",
        mime_type="text/markdown",
    )
    def agent_access_resource() -> str:
        return AGENT_ACCESS_RESOURCE_MD

    return {
        "orig_instructions": orig_instructions,
        "prompts": [
            "stealth_browser_workflow",
            "authenticated_login_workflow",
            "multi_step_2fa_login",
        ],
        "resources": [
            "stealth://guide/overview",
            "stealth://guide/agent-access",
        ],
    }
