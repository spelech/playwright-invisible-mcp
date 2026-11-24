"""Human-gated Bitwarden/Vaultwarden Agent Access (`aac`) credential injection service."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .models import AacStatusResponse, CredentialFillResponse, LoginFormFields

DEFAULT_AAC_PROXY_URL = "wss://ap.lesspassword.dev"
DEFAULT_AAC_TIMEOUT_S = 120
MAX_AAC_TIMEOUT_S = 300
MIN_AAC_TIMEOUT_S = 10

_REMOTE_SESSION_RE = re.compile(r"^\s*-\s+([0-9a-fA-F]{8,64})\b")

DETECT_LOGIN_FIELDS_JS = """() => {
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) {
      return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 4 && r.height > 4;
  };
  const cssEscape = (v) => String(v).replace(/([\\\\\"'\\.#:\\[\\],=])/g, '\\\\$1');
  const buildSelector = (el) => {
    if (el.id) return '#' + cssEscape(el.id);
    const name = el.getAttribute('name');
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (name) {
      const sel = tag + (type ? `[type="${type}"]` : '') + `[name="${cssEscape(name)}"]`;
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    const auto = el.getAttribute('autocomplete');
    if (auto) {
      const sel = `${tag}[autocomplete="${cssEscape(auto)}"]`;
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    if (type) {
      const sel = `${tag}[type="${type}"]`;
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    return null;
  };
  const hay = (el) => [
    el.id || '',
    el.getAttribute('name') || '',
    el.getAttribute('autocomplete') || '',
    el.getAttribute('placeholder') || '',
    el.getAttribute('aria-label') || '',
  ].join(' ').toLowerCase();

  const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
  let passwordEl = inputs.find((el) => (el.type || '').toLowerCase() === 'password') || null;
  let totpEl = inputs.find((el) => {
    const t = (el.type || '').toLowerCase();
    if (t === 'password' || t === 'hidden' || t === 'checkbox' || t === 'radio' || t === 'submit') return false;
    const h = hay(el);
    return h.includes('one-time-code') || /\\b(totp|otp|2fa|mfa|passcode|verification.?code|auth.?code)\\b/.test(h);
  }) || null;
  let usernameEl = inputs.find((el) => {
    if (el === passwordEl || el === totpEl) return false;
    const t = (el.type || '').toLowerCase();
    if (t === 'email') return true;
    if (t !== 'text' && t !== 'tel' && t !== '') return false;
    const h = hay(el);
    return /\\b(user|username|email|e-mail|login|account|acct|identifier|userid|signin)\\b/.test(h);
  }) || null;

  const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], [role="button"]')).filter(isVisible);
  let submitEl = buttons.find((el) => (el.getAttribute('type') || '').toLowerCase() === 'submit') || null;
  if (!submitEl) {
    submitEl = buttons.find((el) => {
      const txt = ((el.innerText || el.value || el.getAttribute('aria-label') || '')).trim().toLowerCase();
      return /^(sign in|log in|login|continue|next|verify|submit|authenticate)$/.test(txt);
    }) || null;
  }

  return {
    usernameSelector: usernameEl ? buildSelector(usernameEl) : null,
    passwordSelector: passwordEl ? buildSelector(passwordEl) : null,
    totpSelector: totpEl ? buildSelector(totpEl) : null,
    submitSelector: submitEl ? buildSelector(submitEl) : null,
  };
}"""

CommandRunner = Callable[[list[str], dict[str, str], float], Awaitable[tuple[int, str, str]]]


def extract_bare_domain(url: str) -> str:
    """Extract a bare hostname suitable for `aac --domain` from a full URL or host string."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    if host.startswith("www.") and len(host) > 4:
        host = host[4:]
    return host


def parse_remote_sessions(connections_output: str) -> list[str]:
    """Parse cached remote-client session fingerprints from `aac connections list` output."""
    sessions: list[str] = []
    in_remote_section = False
    for line in (connections_output or "").splitlines():
        if "Remote Client" in line:
            in_remote_section = True
            continue
        if "User Client" in line:
            in_remote_section = False
            continue
        if in_remote_section:
            match = _REMOTE_SESSION_RE.match(line)
            if match:
                sessions.append(match.group(1))
    return sessions


async def _default_command_runner(
    cmd: list[str],
    env: dict[str, str],
    timeout_s: float,
) -> tuple[int, str, str]:
    """Run an external CLI command asynchronously with a strict wall-clock timeout."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.communicate()
        except Exception:
            pass
        raise
    return (
        int(proc.returncode or 0),
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


class CredentialAccessService:
    """Retrieves human-approved credentials via `aac` and injects them directly into browser forms."""

    def __init__(
        self,
        actions_module: Any,
        *,
        enabled: bool = True,
        aac_bin: str = "aac",
        proxy_url: str = DEFAULT_AAC_PROXY_URL,
        aac_home: Optional[Path] = None,
        session_id: Optional[str] = None,
        bootstrap_token: Optional[str] = None,
        timeout_seconds: int = DEFAULT_AAC_TIMEOUT_S,
        command_runner: Optional[CommandRunner] = None,
    ) -> None:
        self._actions = actions_module
        self.enabled = enabled
        self.aac_bin = aac_bin
        self.proxy_url = proxy_url or DEFAULT_AAC_PROXY_URL
        self.aac_home = aac_home
        self.session_id = (session_id or "").strip() or None
        self._bootstrap_token = (bootstrap_token or "").strip() or None
        self.timeout_seconds = max(MIN_AAC_TIMEOUT_S, min(int(timeout_seconds), MAX_AAC_TIMEOUT_S))
        self._runner: CommandRunner = command_runner or _default_command_runner

    def resolve_binary(self) -> Optional[str]:
        """Resolve the configured `aac` binary path if installed and executable."""
        candidate = Path(self.aac_bin)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return shutil.which(self.aac_bin)

    def _build_env(self, *, include_bootstrap_token: bool = False) -> dict[str, str]:
        env = dict(os.environ)
        if self.aac_home is not None:
            self.aac_home.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(self.aac_home)
        if include_bootstrap_token and self._bootstrap_token:
            env["AAC_TOKEN"] = self._bootstrap_token
        else:
            env.pop("AAC_TOKEN", None)
        return env

    async def list_remote_sessions(self) -> list[str]:
        """Return cached remote-client session fingerprints from `aac connections list`."""
        binary = self.resolve_binary()
        if not self.enabled or not binary:
            return []
        try:
            rc, stdout, _stderr = await self._runner(
                [binary, "connections", "list"],
                self._build_env(include_bootstrap_token=False),
                15.0,
            )
            if rc != 0:
                return []
            return parse_remote_sessions(stdout)
        except Exception:
            return []

    async def detect_login_fields(self, session: Any) -> LoginFormFields:
        """Inspect the active page for visible username, password, TOTP, and submit controls."""
        page = session.page()
        try:
            raw = await page.evaluate(DETECT_LOGIN_FIELDS_JS)
            if isinstance(raw, dict):
                return LoginFormFields(
                    usernameSelector=raw.get("usernameSelector"),
                    passwordSelector=raw.get("passwordSelector"),
                    totpSelector=raw.get("totpSelector"),
                    submitSelector=raw.get("submitSelector"),
                )
        except Exception:
            pass
        return LoginFormFields()

    async def check_status(self, session: Optional[Any] = None) -> str:
        """Return JSON readiness report for Agent Access Control and current page login fields."""
        binary = self.resolve_binary()
        sessions = await self.list_remote_sessions() if (self.enabled and binary) else []
        paired = bool(sessions)

        current_url: Optional[str] = None
        detected_domain: Optional[str] = None
        detected_fields: Optional[LoginFormFields] = None
        if session is not None and session.is_usable():
            current_url = str(session.page().url or "")
            detected_domain = extract_bare_domain(current_url) or None
            detected_fields = await self.detect_login_fields(session)

        if not self.enabled:
            guidance = "Agent Access Control is disabled (STEALTHFOX_AAC_ENABLED=0)."
        elif not binary:
            guidance = (
                f"`{self.aac_bin}` CLI is not installed in this container. Mount or install `aac` "
                "and set STEALTHFOX_AAC_ENABLED=1 to enable human-approved Vaultwarden logins."
            )
        elif paired:
            guidance = (
                "Ready. Navigate to a login page and call `browser_fill_credentials` to request "
                "human-approved Vaultwarden credentials (sent to the user's phone via ntfy) and "
                "inject them directly into the page without exposing secrets in chat."
            )
        elif self._bootstrap_token:
            guidance = (
                "Bootstrap PSK token is configured. Calling `browser_fill_credentials` will pair "
                "automatically on the first request and trigger an ntfy approval push."
            )
        else:
            guidance = (
                "No paired `aac` session cached yet and no STEALTHFOX_AAC_TOKEN is set. Ask the "
                "user to pair this container with `aac listen --reusable-psk`."
            )

        return AacStatusResponse(
            enabled=self.enabled,
            binaryAvailable=bool(binary),
            binaryPath=binary,
            proxyUrl=self.proxy_url,
            homeDir=str(self.aac_home) if self.aac_home else None,
            paired=paired,
            sessions=sessions,
            hasBootstrapToken=bool(self._bootstrap_token),
            currentUrl=current_url,
            detectedDomain=detected_domain,
            detectedFields=detected_fields,
            guidance=guidance,
        ).model_dump_json()

    async def fetch_credential(
        self,
        *,
        domain: Optional[str] = None,
        item_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ) -> tuple[Optional[dict[str, Any]], str, str]:
        """Request a single human-approved credential from `aac`; never log secret values."""
        if not self.enabled:
            return None, "disabled", "Agent Access Control is disabled on this server."
        binary = self.resolve_binary()
        if not binary:
            return None, "disabled", f"`{self.aac_bin}` CLI is not available in this environment."

        sessions = await self.list_remote_sessions()
        use_bootstrap = (not sessions) and bool(self._bootstrap_token)
        if not sessions and not use_bootstrap:
            return (
                None,
                "auth_failed",
                "No paired `aac` session exists. Pair with `aac listen --reusable-psk` first.",
            )

        cmd = [binary, "--proxy-url", self.proxy_url]
        effective_session = self.session_id or (sessions[0] if len(sessions) == 1 else None)
        if effective_session and not use_bootstrap:
            cmd.extend(["--session", effective_session])
        if item_id:
            cmd.extend(["--id", item_id])
        elif domain:
            cmd.extend(["--domain", domain])
        else:
            return None, "error", "Provide a `domain` or `item_id` (or navigate to a site first)."
        cmd.extend(["--output", "json"])

        wait_s = float(
            max(
                MIN_AAC_TIMEOUT_S,
                min(timeout_seconds or self.timeout_seconds, MAX_AAC_TIMEOUT_S),
            )
        )
        try:
            rc, stdout, stderr = await self._runner(
                cmd,
                self._build_env(include_bootstrap_token=use_bootstrap),
                wait_s,
            )
        except asyncio.TimeoutError:
            return (
                None,
                "timeout",
                f"Timed out after {int(wait_s)}s waiting for human approval on ntfy. "
                "Ask the user to tap Approve (or Approve 10m) on their phone and retry.",
            )

        if rc == 0:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                return None, "error", "Received malformed JSON payload from `aac`."
            cred = payload.get("credential") if isinstance(payload, dict) else None
            if not isinstance(cred, dict):
                return None, "not_found", "No credential object returned by `aac`."
            return cred, "ok", "Credential approved and received."

        stderr_lower = (stderr or "").lower()
        if rc == 2:
            return None, "connection_failed", "Could not reach Agent Access relay or host listener."
        if rc == 3:
            return None, "auth_failed", "Agent Access handshake failed (stale session). Clear cached sessions and re-pair."
        if rc == 4:
            return None, "not_found", f"No Vaultwarden login found for `{item_id or domain}`. Verify the bare domain or pass `item_id`."
        if rc == 5:
            return None, "fingerprint_mismatch", "SECURITY ALERT: Agent Access peer fingerprint mismatch. Stop and alert the user."
        if "denied" in stderr_lower or "reject" in stderr_lower:
            return None, "denied", "The user denied the credential request on ntfy."
        if "locked" in stderr_lower or "unlock" in stderr_lower:
            return None, "error", "Vaultwarden vault appears locked. Ask the user to run `/unlock` in `aac-listen`."
        return None, "error", f"`aac` exited with code {rc}."

    async def fill_credentials(
        self,
        session: Any,
        *,
        domain: Optional[str] = None,
        item_id: Optional[str] = None,
        username_selector: Optional[str] = None,
        password_selector: Optional[str] = None,
        totp_selector: Optional[str] = None,
        submit_selector: Optional[str] = None,
        fill_username: bool = True,
        fill_password: bool = True,
        fill_totp: bool = False,
        submit: bool = False,
        press_enter: bool = False,
        timeout_seconds: Optional[int] = None,
    ) -> str:
        """Fetch a human-approved credential via `aac` and type it directly into the active page."""
        t0 = time.monotonic()
        page = session.page()
        url_before = str(page.url or "")
        effective_domain = extract_bare_domain(domain or url_before)

        detected = await self.detect_login_fields(session)
        u_sel = username_selector or (detected.usernameSelector if fill_username else None)
        p_sel = password_selector or (detected.passwordSelector if fill_password else None)
        t_sel = totp_selector or (detected.totpSelector if fill_totp else None)
        s_sel = submit_selector or detected.submitSelector

        if not any((u_sel, p_sel, t_sel)):
            return CredentialFillResponse(
                status="no_fields",
                domain=effective_domain,
                itemId=item_id,
                urlBefore=url_before,
                urlAfter=url_before,
                elapsedMs=round((time.monotonic() - t0) * 1000.0, 2),
                message=(
                    "No matching login or 2FA input fields were found on the current page. "
                    "Navigate to the sign-in form or pass explicit selectors before requesting approval."
                ),
            ).model_dump_json()

        cred, fetch_status, fetch_msg = await self.fetch_credential(
            domain=effective_domain or None,
            item_id=item_id,
            timeout_seconds=timeout_seconds,
        )
        if fetch_status != "ok" or cred is None:
            return CredentialFillResponse(
                status=fetch_status,
                domain=effective_domain,
                itemId=item_id,
                urlBefore=url_before,
                urlAfter=str(page.url or ""),
                elapsedMs=round((time.monotonic() - t0) * 1000.0, 2),
                message=fetch_msg,
            ).model_dump_json()

        used_selectors: dict[str, str] = {}
        available_fields = [k for k in ("username", "password", "totp") if cred.get(k)]
        u_filled = False
        p_filled = False
        t_filled = False
        submitted = False
        try:
            if fill_username and u_sel and cred.get("username"):
                await self._actions.type_text(session, u_sel, str(cred["username"]))
                used_selectors["username"] = u_sel
                u_filled = True
            if fill_password and p_sel and cred.get("password"):
                await self._actions.type_text(session, p_sel, str(cred["password"]))
                used_selectors["password"] = p_sel
                p_filled = True
            if fill_totp and t_sel and cred.get("totp"):
                await self._actions.type_text(session, t_sel, str(cred["totp"]))
                used_selectors["totp"] = t_sel
                t_filled = True

            if submit and s_sel:
                await self._actions.click(session, s_sel)
                used_selectors["submit"] = s_sel
                submitted = True
            elif press_enter:
                await self._actions.press_key(session, "Enter")
                submitted = True
        finally:
            cred.clear()

        url_after = str(page.url or "")
        filled_parts = [
            name
            for name, flag in (("username", u_filled), ("password", p_filled), ("totp", t_filled))
            if flag
        ]
        summary_msg = (
            f"Injected {', '.join(filled_parts)} for `{effective_domain or item_id}` directly into the page"
            + (" and submitted the form." if submitted else ".")
            if filled_parts
            else "Credential was approved, but none of the requested fields were present in the vault item."
        )
        return CredentialFillResponse(
            status="filled" if filled_parts else "no_fields",
            domain=effective_domain,
            itemId=item_id,
            usernameFilled=u_filled,
            passwordFilled=p_filled,
            totpFilled=t_filled,
            submitted=submitted,
            usedSelectors=used_selectors,
            availableFields=available_fields,
            urlBefore=url_before,
            urlAfter=url_after,
            urlChanged=bool(url_before and url_after and url_before != url_after),
            elapsedMs=round((time.monotonic() - t0) * 1000.0, 2),
            message=summary_msg,
        ).model_dump_json()
