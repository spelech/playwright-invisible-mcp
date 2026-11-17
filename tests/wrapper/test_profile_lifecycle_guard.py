"""Tests for ProfileLifecycleGuard lock cleanup, idempotent opens, and auto-open recovery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from wrapper_fakes_work import FakeStealthSession, FakeWorkRuntime

from stealth_wrapper.profile_lifecycle_guard import ProfileLifecycleGuard


@pytest.mark.requirement(
    "GUARD-01", "Clear stale Firefox symlink locks from previous container IPs or dead PIDs"
)
def test_sanitize_profile_holder_clears_foreign_and_dead_locks(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    fake_work = FakeWorkRuntime(str(profile_dir))
    guard = ProfileLifecycleGuard(fake_work, default_profile_dir=str(profile_dir))

    # Boundary 0: None / empty directory or no lock symlink
    assert guard.sanitize_profile_holder(None) is None
    assert guard.sanitize_profile_holder("") is None
    assert guard.sanitize_profile_holder(str(profile_dir)) is None

    # Disturbance 1: Stale symlink from a previous Docker container IP
    lock_file = profile_dir / "lock"
    os.symlink("192.168.32.99:+99999", lock_file)
    assert lock_file.is_symlink()
    assert guard.sanitize_profile_holder(str(profile_dir)) is None
    assert not lock_file.exists()

    # Disturbance 2: Dead local PID on 127.0.0.1
    os.symlink("127.0.0.1:+999999", lock_file)
    assert guard.sanitize_profile_holder(str(profile_dir)) is None
    assert not lock_file.exists()

    # Branch 3: Live local PID (current test runner process) is preserved and reported
    os.symlink(f"127.0.0.1:+{os.getpid()}", lock_file)
    holder = guard.sanitize_profile_holder(str(profile_dir))
    assert holder == f"another Firefox (process {os.getpid()})"
    assert lock_file.is_symlink()
    lock_file.unlink()

    # Disturbance 4: Malformed non-numeric PID target
    os.symlink("127.0.0.1:+notapid", lock_file)
    assert guard.sanitize_profile_holder(str(profile_dir)) == "another Firefox (127.0.0.1:+notapid)"
    lock_file.unlink()


@pytest.mark.requirement(
    "GUARD-02", "Idempotent browser_open reuses healthy sessions and protects main profile"
)
async def test_idempotent_open_reuses_warm_session_and_protects_profile(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    fake_work = FakeWorkRuntime(str(profile_dir))
    guard = ProfileLifecycleGuard(fake_work, default_profile_dir=str(profile_dir))
    guard.install()

    first = await fake_work.open("main")
    assert "opened main" in first
    assert fake_work.open_call_count == 1

    second = await fake_work.open("main", profile="")
    assert "already open and ready" in second
    assert fake_work.open_call_count == 1

    third = await fake_work.open("main", reopen=True)
    assert "opened main" in third
    assert fake_work.open_call_count == 2

    fake_work._open["main"] = FakeStealthSession(usable=True, describe_raises=True)
    fourth = await fake_work.open("main")
    assert "opened main" in fourth
    assert fake_work.open_call_count == 3


@pytest.mark.requirement(
    "GUARD-03",
    "Auto-open interceptors start browser automatically before acting, reading, or typing",
)
async def test_auto_open_interceptors_and_status(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    fake_work = FakeWorkRuntime(str(profile_dir))
    guard = ProfileLifecycleGuard(fake_work, default_profile_dir=str(profile_dir))
    guard.install()

    status_closed = await fake_work.status("main")
    assert "browser is not open" in status_closed
    assert fake_work.open_call_count == 0

    read_val = await fake_work.reading(lambda s: s.describe_pages(), role="main")
    assert len(read_val) == 1
    assert fake_work.open_call_count == 1

    status_open = await fake_work.status("main")
    assert "identity: seed" in status_open

    typed_val = await fake_work.typing(
        lambda _s, sel, txt: _async_val(f"{sel}={txt}"),
        "#search",
        "M3",
        role="support",
    )
    assert typed_val == "#search=M3"
    assert "support" in fake_work._open

    acted_val = await fake_work.acting(
        lambda _s, x: _async_val(x * 2),
        21,
        role="support",
    )
    assert acted_val == 42


async def _async_val(val):
    return val
