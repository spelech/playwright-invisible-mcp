"""Profile lock sanitization, idempotent session opening, and auto-open resilience."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Callable, Optional

from invisible_playwright_mcp.mcp import process
from invisible_playwright_mcp.mcp import work as work_mod
from invisible_playwright_mcp.mcp.server import DEFAULT_BROWSER_ID


class ProfileLifecycleGuard:
    """Manages Docker profile lock cleanup, idempotent opens, and auto-open recovery."""

    def __init__(
        self,
        work_instance: Any,
        default_profile_dir: str = "/data/profile",
    ) -> None:
        self._work = work_instance
        self._default_profile_dir = default_profile_dir
        self._orig_profile_holder: Callable[[Any], Optional[str]] = work_mod.profile_holder
        self._orig_open = work_instance.open
        self._orig_acting = work_instance.acting
        self._orig_reading = work_instance.reading
        self._orig_typing = work_instance.typing
        self._orig_status = work_instance.status

    def sanitize_profile_holder(self, directory: Optional[str]) -> Optional[str]:
        """Clear stale Firefox symlink locks left behind by previous container IPs or dead PIDs."""
        if not directory:
            return None
        folder = Path(directory)
        lock_file = folder / "lock"
        if not lock_file.is_symlink():
            return self._orig_profile_holder(directory)
        try:
            target = os.readlink(lock_file)
        except OSError:
            return None
        host, _, pid = target.rpartition(":+")
        if not pid.isdigit():
            return f"another Firefox ({target})"

        local_ips = {"127.0.0.1"}
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            local_ips.update(ips)
        except Exception:
            pass

        if host in local_ips:
            if not process.alive(int(pid)):
                try:
                    lock_file.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
            return f"another Firefox (process {pid})"

        # The lock was left by a previous container IP on the Docker bridge network
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    async def idempotent_open(
        self,
        role: str = DEFAULT_BROWSER_ID,
        *,
        seed: Optional[int] = None,
        proxy: Optional[str] = None,
        profile: Optional[str] = None,
        reopen: bool = False,
    ) -> str:
        """Open a browser role or reuse the active session if it already matches the identity."""
        at = role or DEFAULT_BROWSER_ID
        if at == DEFAULT_BROWSER_ID and profile == "":
            profile = None

        if not reopen:
            session = self._work._open.get(at)
            launched = self._work._launched.get(at) or {}
            if session is not None and session.is_usable():
                seed_matches = seed is None or seed == launched.get("seed")
                proxy_matches = proxy is None
                expected_profile = (
                    str(Path(profile).expanduser().resolve())
                    if profile
                    else (
                        str(Path(self._default_profile_dir).expanduser().resolve())
                        if at == DEFAULT_BROWSER_ID and self._default_profile_dir
                        else launched.get("profile_dir")
                    )
                )
                profile_matches = profile is None or expected_profile == launched.get("profile_dir")
                if seed_matches and proxy_matches and profile_matches:
                    try:
                        await session.describe_pages()
                        self._work._acted = at
                        status_text = await self._orig_status(at)
                        return f"the {at} browser is already open and ready. {status_text}"
                    except Exception:
                        pass

        return await self._orig_open(at, seed=seed, proxy=proxy, profile=profile)

    async def ensure_browser_open(self, role: Optional[str] = None) -> None:
        """Start fixed roles (`main`, `support`) automatically if no usable session exists."""
        at = role or DEFAULT_BROWSER_ID
        if at not in (DEFAULT_BROWSER_ID, "support"):
            return
        session = self._work._open.get(at)
        if session is None or not session.is_usable():
            await self.idempotent_open(at)

    async def auto_acting(
        self, fn: Callable[..., Any], *args: Any, role: Optional[str] = None, **kwargs: Any
    ) -> Any:
        await self.ensure_browser_open(role)
        return await self._orig_acting(fn, *args, role=role, **kwargs)

    async def auto_reading(
        self, fn: Callable[..., Any], *args: Any, role: Optional[str] = None, **kwargs: Any
    ) -> Any:
        await self.ensure_browser_open(role)
        return await self._orig_reading(fn, *args, role=role, **kwargs)

    async def auto_typing(
        self, fn: Callable[..., Any], selector: str, text: str, role: Optional[str] = None
    ) -> Any:
        await self.ensure_browser_open(role)
        return await self._orig_typing(fn, selector, text, role=role)

    async def auto_status(self, role: Optional[str]) -> str:
        at = role or DEFAULT_BROWSER_ID
        session = self._work._open.get(at)
        if session is None or not session.is_usable():
            return f"the {at} browser is not open. Call browser_open to open it."
        return await self._orig_status(role)

    def install(self) -> None:
        """Install lifecycle hooks onto the upstream work module and instance."""
        work_mod.profile_holder = self.sanitize_profile_holder
        self.sanitize_profile_holder(self._default_profile_dir)
        self._work.open = self.idempotent_open
        self._work.acting = self.auto_acting
        self._work.reading = self.auto_reading
        self._work.typing = self.auto_typing
        self._work.status = self.auto_status

    def restore(self) -> None:
        """Restore original upstream hooks (used by isolated test fixtures)."""
        work_mod.profile_holder = self._orig_profile_holder
        self._work.open = self._orig_open
        self._work.acting = self._orig_acting
        self._work.reading = self._orig_reading
        self._work.typing = self._orig_typing
        self._work.status = self._orig_status
