"""Geometry and timing rules for the live LFO preview research.

This module deliberately has no firmware or emulator dependency.  It describes
the small part of the proposed UI that can be tested without building a
shipping patch: how to locate a stock LFO row, turn a heard value into an
isolated contribution, map that contribution onto a rail, and rate-limit dirty
requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


MIRROR_PREFIX = 34
MIRROR_STRIDE = 202
LFO_FIELDS = ("SPD", "MULT", "FADE", "DEST", "WAVE", "SPH", "MODE", "DEP")
STOCK_LFOS = 3
MAX_RESEARCH_LFOS = 4
# The specialised renderer receives the real stock parameter ids.  The final
# DEP ids are ten apart across the three LFO rows.
WIDGET_DEP_FIRST = 83
BASELINE_VALUE = 0x4000
MAX_UI_HZ = 24.0


class RateTier(str, Enum):
    """The visual treatment that remains legible at a given LFO rate."""

    DOT = "dot"
    BAND = "band"
    FAST_BAND = "fast-band"


def widget_dep_id(lfo: int) -> int:
    """Return the traced stock widget id for an LFO's final DEP cell."""

    if not 0 <= lfo < STOCK_LFOS:
        raise ValueError(f"stock widget ids expose LFO indices 0..{STOCK_LFOS - 1}")
    return WIDGET_DEP_FIRST + 10 * lfo


def parameter_slot(lfo: int, field: int | str) -> int:
    """Return the runtime mirror slot for an LFO field.

    ``lfo`` is zero-based.  Stock LFO1..3 therefore occupy slots 1..24.
    LFO4 is accepted here because the extension research uses the same eight
    field shape, even though stock 1.11 has no fourth row.
    """

    if not 0 <= lfo < MAX_RESEARCH_LFOS:
        raise ValueError(f"LFO index must be 0..{MAX_RESEARCH_LFOS - 1}")
    if isinstance(field, str):
        try:
            field = LFO_FIELDS.index(field.upper())
        except ValueError as exc:
            raise ValueError(f"unknown LFO field: {field}") from exc
    if not 0 <= field < len(LFO_FIELDS):
        raise ValueError(f"LFO field must be 0..{len(LFO_FIELDS) - 1}")
    return 1 + lfo * len(LFO_FIELDS) + field


def mirror_offset(track: int, lfo: int, field: int | str) -> int:
    """Return the byte offset of an LFO field in a per-track mirror."""

    if track < 0 or track >= 16:
        raise ValueError("track must be 0..15")
    return MIRROR_PREFIX + MIRROR_STRIDE * track + 2 * parameter_slot(lfo, field)


def mirror_address(base: int, track: int, lfo: int, field: int | str) -> int:
    """Return an LFO field address for a measured mirror base."""

    return base + mirror_offset(track, lfo, field)


def state_record_address(base: int, track: int, lfo: int) -> int:
    """Return the stock evaluator-A state record for one track and LFO.

    The stock state is 16 tracks x 3 records x 40 bytes.  The evaluator walks
    the three records in a 120-byte track slice.  LFO4 therefore has no
    verified stock state address and is intentionally rejected here.
    """

    if track < 0 or track >= 16:
        raise ValueError("track must be 0..15")
    if lfo < 0 or lfo >= STOCK_LFOS:
        raise ValueError(f"stock state addresses expose LFO indices 0..{STOCK_LFOS - 1}")
    return base + track * 120 + lfo * 40


def state_phase_address(base: int, track: int, lfo: int) -> int:
    """Return the phase field used by the evaluator probe."""

    return state_record_address(base, track, lfo)


def state_output_address(base: int, track: int, lfo: int) -> int:
    """Return the evaluator output field for a stock state record."""

    return state_record_address(base, track, lfo) + 4


def isolated_contribution(heard_value: int, target_value: int = BASELINE_VALUE) -> int:
    """Return a signed contribution in the destination's native 16-bit scale.

    A manual probe can isolate one LFO and centre every other source.  In that
    setup, ``heard - target`` is a useful control value because it includes
    depth, fade and destination clipping.  The firmware preview does not use
    this helper.  Its hook captures the pre-accumulation value directly.
    Clamping belongs to the destination evaluator, so a clipped result remains
    useful evidence here.
    """

    return int(heard_value) - int(target_value)


def rate_tier(rate_hz: float) -> RateTier:
    """Choose the dot, dither band, or fast band treatment."""

    if not math.isfinite(rate_hz) or rate_hz < 0:
        raise ValueError("rate must be a finite, non-negative number")
    if rate_hz <= 3.0:
        return RateTier.DOT
    if rate_hz <= 12.0:
        return RateTier.BAND
    return RateTier.FAST_BAND


def lfo_rate_hz(speed: float, multiplier: float, bpm: float) -> float:
    """Convert the displayed speed and multiplier to the manual's Hz formula."""

    if multiplier < 0 or bpm < 0:
        raise ValueError("multiplier and BPM must be non-negative")
    return abs(speed) * multiplier * bpm / 30720.0


def rail_y(offset: int | float, half_range: int | float,
           top: int = 13, bottom: int = 57) -> int:
    """Map a signed contribution to a vertical rail, clamped to its ends."""

    if half_range <= 0:
        raise ValueError("half_range must be positive")
    if bottom <= top:
        raise ValueError("bottom must be below top")
    fraction = (float(offset) + half_range) / (2.0 * half_range)
    fraction = min(1.0, max(0.0, fraction))
    return round(bottom - fraction * (bottom - top))


def redraw_due(last_value: int | None, new_value: int, now_s: float,
               last_request_s: float = -math.inf, *, visible: bool = True,
               max_hz: float = MAX_UI_HZ) -> bool:
    """Whether a visible indicator needs another dirty-screen request.

    The caller stores ``new_value`` and ``now_s`` only when this returns true.
    A value that has not changed never creates a refresh, and an off-page
    indicator never creates one either.
    """

    if not visible or last_value == new_value:
        return False
    if not math.isfinite(now_s) or max_hz <= 0:
        raise ValueError("now_s must be finite and max_hz must be positive")
    return now_s - last_request_s >= 1.0 / max_hz


@dataclass(frozen=True)
class RailGeometry:
    """The measured source-pixel geometry used by the disposable mockup."""

    x: int = 18
    top: int = 13
    bottom: int = 57
    centre: int = 35
    width: int = 1
    dot_width: int = 3
