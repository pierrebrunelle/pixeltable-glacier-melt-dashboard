"""Glacier station melt dashboard, built with Pixeltable.

`Stations` lists the monitoring stations on four Swiss glaciers. `Readings` holds one
row per station and day: air temperature plus a JSON list of ablation-stake readings
(`[{"stake": "S1", "ablation_cm": 4.2, "ok": true}, ...]`) and sensor flags.
Computed columns clean the stake list with Pixeltable's built-in JSON array functions
(`filter`, `map`, `sort`), so the dashboard queries only aggregate
(`count`, `sum`, `mean`, `min`, `max`, `group_by`, `distinct`).

Layout: UDFs live in udfs.py; this file holds the table models and the dashboard queries.
No HTTP router.

    pxt schema update app.py glacier
    python dashboard.py seed
    python dashboard.py build
"""

from __future__ import annotations

import pixeltable as pxt
from pixeltable.functions import count, filter, map, max, mean, min, sort, sum

from udfs import mean_of, melt_band, water_equivalent_mm

TableModel = pxt.model_base()


class Stations(TableModel, name="stations"):
    """One monitoring station on a glacier."""

    station_id = pxt.Column(type=pxt.String, primary_key=True)
    glacier: pxt.String
    region: pxt.String
    elevation_m: pxt.Int
    kind: pxt.String


class Readings(TableModel, name="readings"):
    """One station-day: air temperature, ablation stakes and sensor flags."""

    station_id: pxt.String
    day: pxt.Date
    temp_c: pxt.Float | None
    stakes: pxt.Json
    flags: pxt.Json

    # Built-in JSON array functions: keep working stakes, pull their values, rank them.
    good_stakes = filter(stakes, lambda x: x.ok == True)  # noqa: E712 (expression, not a Python bool)
    ablation = map(good_stakes, lambda x: x.ablation_cm)
    ranked = sort(good_stakes, key=lambda x: x.ablation_cm, asc=False)
    top_stake = ranked[0].stake.astype(pxt.String | None)
    n_ok = good_stakes.len().astype(pxt.Int | None)
    melt_cm = mean_of(ablation)
    water_mm = water_equivalent_mm(melt_cm)
    band = melt_band(melt_cm)
    n_flags = flags.len().astype(pxt.Int | None)

    __indexes__ = [pxt.BtreeIndex(station_id), pxt.BtreeIndex(day)]


def station_days(station_id: str) -> pxt.Query:
    """Daily rows of one station, newest first."""
    return (
        Readings.table.where(Readings.table.station_id == station_id)
        .order_by(Readings.table.day, asc=False)
        .select(
            Readings.table.day,
            Readings.table.temp_c,
            Readings.table.n_ok,
            Readings.table.melt_cm,
            Readings.table.water_mm,
            Readings.table.band,
        )
    )


def station_summary() -> pxt.Query:
    """Per-station aggregate for the dashboard."""
    r = Readings.table
    return (
        r.group_by(r.station_id)
        .order_by(r.station_id)
        .select(
            r.station_id,
            days=count(r.day),
            mean_temp=mean(r.temp_c),
            min_temp=min(r.temp_c),
            max_temp=max(r.temp_c),
            total_melt_cm=sum(r.melt_cm),
            total_water_mm=sum(r.water_mm),
            peak_melt_cm=max(r.melt_cm),
            flags=sum(r.n_flags),
        )
    )
