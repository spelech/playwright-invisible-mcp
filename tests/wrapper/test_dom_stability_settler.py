"""Tests for DomStabilitySettler navigation and route-action DOM stability settling."""

from __future__ import annotations

import pytest
from wrapper_fakes_page import FakeStealthPage
from wrapper_fakes_work import FakeStealthSession

from stealth_wrapper.dom_stability_settler import DomStabilitySettler


class DummyActionsModule:
    """In-memory actions namespace for testing DomStabilitySettler installation."""

    def __init__(self) -> None:
        self.last_wait_until: str = ""

    async def navigate(
        self, session: FakeStealthSession, url: str, wait_until: str = "domcontentloaded"
    ) -> str:
        self.last_wait_until = wait_until
        session.page().url = url
        return f"navigated to {url} (HTTP 200)"

    async def click(self, session: FakeStealthSession, selector: str) -> str:
        if selector == "#route-link":
            session.page().url = "https://www.mcmaster.com/products/enclosure-lights/"
            session.page().mutate_calls_remaining = 1
        return f"clicked {selector}"

    async def press_key(self, _session: FakeStealthSession, key: str) -> str:
        return f"pressed {key}"


@pytest.mark.requirement(
    "TRANS-01", "Bounded DOM stability settler maps networkidle to load and settles mutating pages"
)
async def test_navigate_maps_networkidle_and_settles_dom() -> None:
    dummy_actions = DummyActionsModule()
    settler = DomStabilitySettler(navigation_settle_ms=600, action_settle_ms=600)
    settler.install(dummy_actions)

    page = FakeStealthPage()
    page.mutate_calls_remaining = 1
    session = FakeStealthSession(page=page)

    result = await dummy_actions.navigate(
        session,
        "https://www.mcmaster.com/5278N42/",
        wait_until="networkidle",
    )
    assert result == "navigated to https://www.mcmaster.com/5278N42/ (HTTP 200)"
    assert dummy_actions.last_wait_until == "load"
    assert page.mutate_calls_remaining == 0


@pytest.mark.requirement(
    "TRANS-02",
    "Route-changing actions wait for resource timeline and DOM stability while non-route clicks return immediately",
)
async def test_route_action_settles_only_when_url_changes() -> None:
    dummy_actions = DummyActionsModule()
    settler = DomStabilitySettler(navigation_settle_ms=500, action_settle_ms=500)
    settler.install(dummy_actions)

    page = FakeStealthPage(url="https://www.mcmaster.com/products/lights/")
    session = FakeStealthSession(page=page)

    res_same = await dummy_actions.click(session, "#stay-here")
    assert res_same == "clicked #stay-here"
    assert page.url == "https://www.mcmaster.com/products/lights/"

    res_route = await dummy_actions.click(session, "#route-link")
    assert res_route == "clicked #route-link"
    assert page.url == "https://www.mcmaster.com/products/enclosure-lights/"
    assert page.mutate_calls_remaining == 0

    await settler.wait_for_stability(page, 0)
    await settler.wait_for_stability(None, 500)
