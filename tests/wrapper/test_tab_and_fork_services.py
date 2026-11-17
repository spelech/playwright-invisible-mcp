"""Unit tests for TabRoutingService and SessionForkService."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from wrapper_fakes_page import FakeBrowser
from wrapper_fakes_work import FakeStealthSession, FakeWorkRuntime

from stealth_wrapper.models import (
    ForkCreateResponse,
    ForkDiscardResponse,
    ForkPromoteResponse,
    TabActionResponse,
    TabListResponse,
)
from stealth_wrapper.profile_lifecycle_guard import ProfileLifecycleGuard
from stealth_wrapper.session_fork_service import SessionForkService


class _FakeActionsForTabs:
    @staticmethod
    async def navigate(
        session: FakeStealthSession, url: str, wait_until: str = "domcontentloaded"
    ) -> str:
        del wait_until
        session.page().url = url
        session.page().page_title = f"Title for {url}"
        return f"navigated to {url} (HTTP 200)"


@pytest.mark.requirement(
    "MCP-08",
    "Same-session multi-tab management preserves pinned tab 0 and supports inline tab_id routing",
)
async def test_tab_routing_pinned_and_scoped_navigation(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    fake_work = FakeWorkRuntime(str(profile_dir))
    await fake_work.open("main")
    main_session = fake_work._open["main"]
    main_session.page().url = "https://www.mcmaster.com/orders"
    main_session.page().page_title = "Order History"

    service = SessionForkService(
        fake_work,
        actions_module=_FakeActionsForTabs,
        host_browser_factory=lambda **_kw: FakeBrowser(),
    )
    service.install()
    try:
        created_1 = TabActionResponse.model_validate_json(
            await service.new_tab(
                main_session,
                url="https://www.mcmaster.com/91251A197/",
                activate=False,
                session_id="main",
            )
        )
        assert created_1.status == "created"
        assert created_1.tabId == 1
        assert created_1.activeTabId == 0
        assert created_1.active is False
        assert main_session.page().url == "https://www.mcmaster.com/orders"

        created_2 = TabActionResponse.model_validate_json(
            await service.new_tab(
                main_session,
                url="https://www.mcmaster.com/98164A120/",
                activate=False,
                session_id="main",
            )
        )
        assert created_2.tabId == 2
        assert created_2.tabCount == 3
        assert main_session.page().url == "https://www.mcmaster.com/orders"

        with service.tab_scope(1):
            assert main_session.page().url == "https://www.mcmaster.com/91251A197/"
        assert main_session.page().url == "https://www.mcmaster.com/orders"

        selected = TabActionResponse.model_validate_json(
            await service.select_tab(main_session, 1, session_id="main")
        )
        assert selected.activeTabId == 1
        assert main_session.page().url == "https://www.mcmaster.com/91251A197/"

        closed = TabActionResponse.model_validate_json(
            await service.close_tab(main_session, tab_id=1, session_id="main")
        )
        assert closed.status == "closed"
        assert closed.tabId == 1
        assert closed.tabCount == 2

        tab_list = TabListResponse.model_validate_json(
            await service.list_tabs(main_session, session_id="main")
        )
        assert [t.tabId for t in tab_list.tabs] == [0, 2]

        with pytest.raises(RuntimeError, match="tab 1 is not open"):
            with service.tab_scope(1):
                main_session.page()
    finally:
        service.restore()


@pytest.mark.requirement(
    "MCP-09",
    "Context forks snapshot storage_state into isolated contexts without contending on main locks",
)
async def test_fork_session_parallel_isolation_and_checkpoint_rollback(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    fake_work = FakeWorkRuntime(str(profile_dir))
    await fake_work.open("main")
    main_session: FakeStealthSession = fake_work._open["main"]
    main_session.page().url = "https://www.mcmaster.com/orders"
    main_session._browser = None

    shared_host = FakeBrowser()
    host_launches = 0

    def _make_host(**_kw: Any) -> FakeBrowser:
        nonlocal host_launches
        host_launches += 1
        return shared_host

    service = SessionForkService(
        fake_work,
        actions_module=_FakeActionsForTabs,
        host_browser_factory=_make_host,
    )
    service.install()
    try:
        fork_1 = ForkCreateResponse.model_validate_json(
            await service.fork_session(
                from_browser="main",
                session_id="cat-screws",
                url="https://www.mcmaster.com/screws/",
            )
        )
        fork_2 = ForkCreateResponse.model_validate_json(
            await service.fork_session(
                from_browser="main",
                session_id="cat-nuts",
                url="https://www.mcmaster.com/nuts/",
            )
        )
        assert host_launches == 1
        assert fork_1.sessionId == "cat-screws"
        assert fork_1.cookieCount == 2
        assert fork_1.originCount == 1
        assert fork_1.url == "https://www.mcmaster.com/screws/"
        assert fork_2.sessionId == "cat-nuts"

        main_lock = fake_work._input_locks.setdefault("main", asyncio.Lock())
        async with main_lock:
            res = await fake_work.acting(
                _FakeActionsForTabs.navigate,
                "https://www.mcmaster.com/screws/socket-head/",
                role="cat-screws",
            )
            assert "socket-head" in res

        listed = await fake_work.listing()
        assert "cat-screws" in listed["note"]
        assert len(listed["browsers"]) == 3

        fork_2_sess = fake_work._open["cat-nuts"]
        await fork_2_sess._context.add_cookies(
            [
                {
                    "name": "SpeculativeCart",
                    "value": "deleted-all",
                    "domain": ".mcmaster.com",
                    "path": "/",
                }
            ]
        )
        discarded = ForkDiscardResponse.model_validate_json(await service.discard_fork("cat-nuts"))
        assert discarded.status == "discarded"
        assert "cat-nuts" not in fake_work._open
        main_cookies = await main_session._context.cookies()
        assert all(c["name"] != "SpeculativeCart" for c in main_cookies)

        fork_1_sess = fake_work._open["cat-screws"]
        await fork_1_sess._context.add_cookies(
            [
                {
                    "name": "ApprovedSession",
                    "value": "promoted-123",
                    "domain": ".mcmaster.com",
                    "path": "/",
                }
            ]
        )
        fork_1_sess._context._local_storage["https://www.mcmaster.com"] = {
            "CartCheckpoint": "v2-approved"
        }
        promoted = ForkPromoteResponse.model_validate_json(
            await service.promote_fork(
                "cat-screws",
                target_browser="main",
                close_fork=True,
                navigate=True,
            )
        )
        assert promoted.status == "promoted"
        assert promoted.cookiesApplied >= 3
        assert promoted.originsSynced == 1
        assert promoted.forkClosed is True
        assert promoted.navigated is True
        assert "cat-screws" not in fake_work._open
        assert main_session.page().url == "https://www.mcmaster.com/screws/socket-head/"
        assert any(c["name"] == "ApprovedSession" for c in await main_session._context.cookies())
        assert (
            main_session._context._local_storage["https://www.mcmaster.com"]["CartCheckpoint"]
            == "v2-approved"
        )

        with pytest.raises(ValueError, match="reserved"):
            await service.fork_session(from_browser="main", session_id="main")
        with pytest.raises(ValueError, match="cannot be discarded"):
            await service.discard_fork("main")
        with pytest.raises(RuntimeError, match="is not open"):
            await service.discard_fork("cat-missing")

        await fake_work.close_all()
        assert service._host_browser is None
    finally:
        service.restore()


@pytest.mark.requirement(
    "SEC-02", "ProfileLifecycleGuard does not auto-launch rogue browsers for missing fork IDs"
)
async def test_lifecycle_guard_skips_auto_open_for_unknown_forks(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    fake_work = FakeWorkRuntime(str(profile_dir))
    guard = ProfileLifecycleGuard(fake_work, default_profile_dir=fake_work.default_profile_dir)
    guard.install()

    with pytest.raises(RuntimeError, match="not open"):
        await fake_work.acting(
            _FakeActionsForTabs.navigate, "https://example.com", role="fork-ghost"
        )
    assert "fork-ghost" not in fake_work._open
    assert fake_work.open_call_count == 0
