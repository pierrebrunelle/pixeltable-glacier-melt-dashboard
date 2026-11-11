"""UDFs for the glacier station melt dashboard. Pure Python, no table imports."""

from __future__ import annotations

import pixeltable as pxt

ICE_DENSITY = 0.9  # water equivalent of 1 cm of glacier ice, in cm w.e.

# Daily melt bands used on the dashboard (cm of ice per day, mean over working stakes).
BANDS = [(1.0, "frozen"), (6.0, "low"), (12.0, "moderate"), (18.0, "high")]


@pxt.udf
def melt_band(melt_cm: float | None) -> str | None:
    """Label a station-day by its mean stake ablation."""
    if melt_cm is None:
        return None
    for limit, label in BANDS:
        if melt_cm < limit:
            return label
    return "extreme"


@pxt.udf
def water_equivalent_mm(melt_cm: float | None) -> float | None:
    """Convert ice ablation (cm) into millimetres of water equivalent."""
    if melt_cm is None:
        return None
    return round(melt_cm * 10.0 * ICE_DENSITY, 1)


@pxt.udf
def mean_of(values: pxt.Json) -> float | None:
    """Mean of a JSON list of numbers; None for an empty or missing list."""
    if not isinstance(values, list):
        raise TypeError(f"expected a list of stake readings, got {type(values).__name__}")
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 2) if nums else None
