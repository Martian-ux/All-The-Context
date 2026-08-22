"""Focused Wave 2 Packet D disposable zero-dashboard journey tests."""

from __future__ import annotations

from pathlib import Path

from allthecontext.experimental_zero_dashboard_harness import (
    ZeroDashboardFixture,
    run_zero_dashboard_journey,
)


def test_zero_dashboard_wave2_journey_passes_every_non_compensable_gate(
    tmp_path: Path,
) -> None:
    fixture = ZeroDashboardFixture.from_json(
        Path(__file__).resolve().parents[1] / "fixtures" / "zero_dashboard_wave2.json"
    )

    receipt = run_zero_dashboard_journey(tmp_path / "zero-dashboard.sqlite3", fixture=fixture)

    assert receipt.scorecard.passed is True, receipt.scorecard.as_dict()
    assert receipt.scorecard.as_dict()["passed"] is True
    assert "Atlas uses deterministic local retrieval." in receipt.first_context
    assert "Atlas private staging uses a bounded fixture." in receipt.first_context
    assert "Atlas uses bounded local retrieval." in receipt.corrected_context
    assert "Atlas uses deterministic local retrieval." not in receipt.corrected_context
    assert "Neptune uses a separate source." not in receipt.final_context
    assert "Expired Atlas working-state fixture." not in receipt.final_context
    assert "Temporary deletion fixture for Atlas." not in receipt.final_context
    assert "Terminal purge fixture for Atlas." not in receipt.final_context
    assert "Atlas private staging uses a bounded fixture." not in receipt.viewer_context
    assert receipt.capture_event_count == 6
    assert receipt.observation_count >= 7


def test_default_zero_dashboard_fixture_is_deterministic_and_sanitized() -> None:
    from allthecontext.experimental_zero_dashboard_harness import (
        default_zero_dashboard_fixture,
    )

    fixture = default_zero_dashboard_fixture()

    assert [event.provider_event_id for page in fixture.pages for event in page.events] == [
        "capture-project",
        "capture-private",
        "capture-delete-target",
        "capture-other-project",
        "capture-expired",
        "capture-delete",
    ]
    assert all(
        "token:" not in str(event.payload).casefold()
        for page in fixture.pages
        for event in page.events
    )
