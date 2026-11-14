"""Unit tests for the disposable live LFO indicator model."""

import pytest

from dnfw.lfo_preview import (
    BASELINE_VALUE,
    RateTier,
    isolated_contribution,
    lfo_rate_hz,
    mirror_address,
    mirror_offset,
    parameter_slot,
    rail_y,
    redraw_due,
    state_output_address,
    widget_dep_id,
)


def test_stock_lfo_slots_are_contiguous_and_lfo4_has_the_expected_extension_shape():
    assert parameter_slot(0, "SPD") == 1
    assert parameter_slot(1, "DEP") == 16
    assert parameter_slot(2, "SPD") == 17
    assert parameter_slot(2, "DEP") == 24
    assert parameter_slot(3, "SPD") == 25
    assert parameter_slot(3, "DEP") == 32
    assert mirror_offset(3, 2, "DEST") == 34 + 202 * 3 + 2 * 20
    assert mirror_address(0x80000000, 0, 0, "SPD") == 0x80000000 + 36


def test_stock_lfo_dep_widget_ids_are_the_traced_view_ids():
    assert [widget_dep_id(i) for i in range(3)] == [83, 93, 103]
    with pytest.raises(ValueError, match="stock widget ids"):
        widget_dep_id(3)


def test_state_output_is_four_bytes_into_each_40_byte_record():
    assert state_output_address(0x4463FC18, 0, 0) == 0x4463FC1C
    assert state_output_address(0x4463FC18, 0, 2) == 0x4463FC6C
    assert state_output_address(0x4463FC18, 3, 2) == 0x4463FDD4
    with pytest.raises(ValueError, match="stock state addresses"):
        state_output_address(0x4463FC18, 0, 3)


def test_isolated_contribution_preserves_sign_and_zero_depth():
    assert isolated_contribution(BASELINE_VALUE) == 0
    assert isolated_contribution(BASELINE_VALUE + 0x120) == 0x120
    assert isolated_contribution(BASELINE_VALUE - 0x120) == -0x120


def test_rate_tiers_and_manual_rate_formula():
    from dnfw.lfo_preview import rate_tier

    assert rate_tier(3.0) is RateTier.DOT
    assert rate_tier(3.01) is RateTier.BAND
    assert rate_tier(12.0) is RateTier.BAND
    assert rate_tier(12.01) is RateTier.FAST_BAND
    assert lfo_rate_hz(-48, 1, 120) == pytest.approx(0.1875)
    with pytest.raises(ValueError):
        rate_tier(float("nan"))


def test_rail_is_centre_at_zero_and_clamps():
    assert rail_y(0, 100, top=13, bottom=57) == 35
    assert rail_y(100, 100, top=13, bottom=57) == 13
    assert rail_y(-100, 100, top=13, bottom=57) == 57
    assert rail_y(1000, 100, top=13, bottom=57) == 13


def test_redraw_only_when_visible_value_changes_and_24hz_budget_allows_it():
    assert redraw_due(None, 1, 0.0)
    assert not redraw_due(1, 1, 0.1)
    assert not redraw_due(1, 2, 0.01, 0.0)
    assert redraw_due(1, 2, 1 / 24, 0.0)
    assert not redraw_due(1, 2, 1.0, 0.0, visible=False)
