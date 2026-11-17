"""Bounded DOM-stability settling for single-page application navigations and route transitions."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

_DOM_FINGERPRINT_JS = (
    "(() => {"
    " const b = document.body;"
    " const t = b ? (b.innerText || '') : '';"
    " let h = 0;"
    " const step = Math.max(1, Math.floor(t.length / 500));"
    " for (let i = 0; i < t.length; i += step) { h = (h * 31 + t.charCodeAt(i)) | 0; }"
    " const r = performance.getEntriesByType('resource').length;"
    " const c = document.querySelectorAll('a,button,input,select,table,tr').length;"
    " return r + ':' + t.length + ':' + c + ':' + h;"
    "})()"
)


class DomStabilitySettler:
    """Monitors resource timeline and DOM layout stability after navigations and route-changing clicks."""

    def __init__(
        self,
        navigation_settle_ms: int = 2500,
        action_settle_ms: int = 2500,
    ) -> None:
        self.navigation_settle_ms = navigation_settle_ms
        self.action_settle_ms = action_settle_ms
        self._originals: dict[str, Any] = {}

    async def wait_for_stability(
        self,
        page: Any,
        max_ms: int,
        *,
        require_resource_change_from: Optional[int] = None,
        stable_window_s: float = 0.32,
        no_resource_fallback_s: float = 1.2,
    ) -> None:
        """Poll resource count and DOM fingerprint until stable for `stable_window_s` or `max_ms` elapses."""
        if max_ms <= 0 or page is None:
            return
        start = time.monotonic()
        deadline = start + (max_ms / 1000.0)
        last: Optional[str] = None
        stable_since = start
        saw_new = require_resource_change_from is None
        saw_non_empty = False

        while time.monotonic() < deadline:
            try:
                cur = str(await page.evaluate(_DOM_FINGERPRINT_JS))
            except Exception:
                break
            now = time.monotonic()
            parts = cur.split(":")
            if len(parts) >= 3:
                res_count = int(parts[0]) if parts[0].isdigit() else 0
                text_len = int(parts[1]) if parts[1].isdigit() else 0
                elem_count = int(parts[2]) if parts[2].isdigit() else 0
                if (
                    require_resource_change_from is not None
                    and res_count > require_resource_change_from
                ):
                    saw_new = True
                if text_len > 40 or elem_count > 5:
                    saw_non_empty = True

            if cur != last:
                last = cur
                stable_since = now
            elif saw_new and saw_non_empty and (now - stable_since) >= stable_window_s:
                break

            if not saw_new and (now - start) >= no_resource_fallback_s:
                break
            await asyncio.sleep(0.12)

    def wrap_navigate(self, orig_navigate: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap upstream `actions.navigate` with bounded DOM stability settling."""

        async def _settled_navigate(
            session: Any,
            url: str,
            wait_until: str = "domcontentloaded",
        ) -> str:
            effective_wait = "load" if wait_until == "networkidle" else wait_until
            answer = await orig_navigate(session, url, effective_wait)
            if self.navigation_settle_ms > 0:
                try:
                    await self.wait_for_stability(
                        session.page(),
                        self.navigation_settle_ms,
                        stable_window_s=0.35,
                    )
                except Exception:
                    pass
            return answer

        _settled_navigate.__name__ = getattr(orig_navigate, "__name__", "navigate")
        return _settled_navigate

    def wrap_route_action(self, orig_action: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap an interaction tool so URL-changing actions wait for route hydration."""

        async def _wrapped(session: Any, *args: Any, **kwargs: Any) -> Any:
            page = None
            before = None
            n0 = 0
            try:
                page = session.page()
                before = page.url
                n0 = int(await page.evaluate("performance.getEntriesByType('resource').length"))
            except Exception:
                pass

            answer = await orig_action(session, *args, **kwargs)

            try:
                if (
                    self.action_settle_ms > 0
                    and before is not None
                    and page is not None
                    and page.url != before
                ):
                    await self.wait_for_stability(
                        page,
                        self.action_settle_ms,
                        require_resource_change_from=n0,
                        stable_window_s=0.32,
                        no_resource_fallback_s=1.2,
                    )
            except Exception:
                pass
            return answer

        _wrapped.__name__ = getattr(orig_action, "__name__", "action")
        return _wrapped

    def install(self, actions_module: Any) -> None:
        """Install navigation and route-action stability wrappers onto `actions`."""
        self._originals["navigate"] = actions_module.navigate
        actions_module.navigate = self.wrap_navigate(actions_module.navigate)
        for action_name in ("click", "click_at", "press_key", "select_option"):
            action_fn = getattr(actions_module, action_name, None)
            if action_fn is not None:
                self._originals[action_name] = action_fn
                setattr(actions_module, action_name, self.wrap_route_action(action_fn))

    def restore(self, actions_module: Any) -> None:
        """Restore original upstream action functions (used by isolated test fixtures)."""
        for name, orig in self._originals.items():
            setattr(actions_module, name, orig)
        self._originals.clear()
