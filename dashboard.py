"""glacier: seed station readings, build a static melt dashboard, inspect runtime health.

    pxt schema update app.py glacier
    python dashboard.py seed            # load stations + daily readings (one malformed row on purpose)
    python dashboard.py build           # write output/dashboard.md and output/dashboard.html
    python dashboard.py station RHO-01  # daily rows of one station
    python dashboard.py debug           # computed-column errors + daemon status/health

Every command takes --target: a local catalog dir (default 'glacier') or a
Pixeltable Cloud catalog URI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import subprocess
from pathlib import Path

import pixeltable as pxt
import pixeltable.functions as pxtf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"


def start(target: str):
    import app

    app.TableModel.bind_all(target)
    return app


def table_md(rows: list[dict]) -> str:
    if not rows:
        return "_(no rows)_\n"
    cols = list(rows[0].keys())
    fmt = lambda v: f"{v:.2f}" if isinstance(v, float) else ("" if v is None else str(v))  # noqa: E731
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def table_html(rows: list[dict]) -> str:
    if not rows:
        return "<p><em>no rows</em></p>"
    cols = list(rows[0].keys())
    fmt = lambda v: f"{v:.2f}" if isinstance(v, float) else ("" if v is None else str(v))  # noqa: E731
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(fmt(r[c]))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def cmd_seed(args) -> None:
    app = start(args.target)
    stations = json.loads((DATA / "stations.json").read_text())
    readings = json.loads((DATA / "readings.json").read_text())
    for r in readings:
        r["day"] = dt.date.fromisoformat(r["day"])
    s1 = app.Stations.table.batch_update(stations, if_not_exists="insert")
    # Rows whose stakes field is not a JSON list (a logger glitch) go in their own insert,
    # so the batch of well-formed readings is never held up by them.
    good = [r for r in readings if isinstance(r["stakes"], list)]
    odd = [r for r in readings if not isinstance(r["stakes"], list)]
    s2 = app.Readings.table.insert(good, on_error="ignore")
    n_odd = sum(app.Readings.table.insert([r], on_error="ignore").num_rows for r in odd)
    print(
        f"stations: {s1.num_rows} rows written · readings: {s2.num_rows} rows, {s2.num_excs} cell errors"
        f" · {n_odd} malformed reading(s) loaded separately"
    )


def sections(app) -> list[tuple[str, list[dict]]]:
    r, s = app.Readings.table, app.Stations.table
    out = []
    out.append(("Stations", [dict(x) for x in app.station_summary().collect()]))
    j = r.join(s, on=r.station_id == s.station_id)
    out.append(
        (
            "Glaciers",
            [
                dict(x)
                for x in j.group_by(s.glacier)
                .order_by(s.glacier)
                .select(
                    s.glacier,
                    station_days=pxtf.count(r.day),
                    mean_melt_cm=pxtf.mean(r.melt_cm),
                    total_water_mm=pxtf.sum(r.water_mm),
                    warmest_c=pxtf.max(r.temp_c),
                    coldest_c=pxtf.min(r.temp_c),
                )
                .collect()
            ],
        )
    )
    out.append(
        (
            "Melt bands",
            [dict(x) for x in r.group_by(r.band).order_by(r.band).select(r.band, days=pxtf.count(r.day)).collect()],
        )
    )
    out.append(
        (
            "Distinct values",
            [
                {"what": "regions", "values": ", ".join(sorted(x["region"] for x in s.select(s.region).distinct().collect()))},
                {"what": "kinds", "values": ", ".join(sorted(x["kind"] for x in s.select(s.kind).distinct().collect()))},
                {"what": "top stakes", "values": ", ".join(sorted(str(x["top_stake"]) for x in r.select(r.top_stake).distinct().collect()))},
                {"what": "bands", "values": ", ".join(sorted(str(x["band"]) for x in r.select(r.band).distinct().collect()))},
            ],
        )
    )
    top = (
        r.where(r.melt_cm != None)  # noqa: E711
        .order_by(r.melt_cm, asc=False)
        .limit(5)
        .select(r.station_id, r.day, r.temp_c, r.melt_cm, r.water_mm, r.top_stake, r.n_ok)
        .collect()
    )
    out.append(("Top melt days", [dict(x) for x in top]))
    return out


def cmd_build(args) -> None:
    app = start(args.target)
    secs = sections(app)
    OUT.mkdir(exist_ok=True)
    md = ["# Glacier station melt dashboard", "", f"Built {dt.datetime.now():%Y-%m-%d %H:%M} from `{args.target}`.", ""]
    body = []
    for title, rows in secs:
        md += [f"## {title}", "", table_md(rows)]
        body.append(f"<h2>{html.escape(title)}</h2>{table_html(rows)}")
    (OUT / "dashboard.md").write_text("\n".join(md))
    style = "body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}"
    (OUT / "dashboard.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>Glacier melt dashboard</title><style>{style}</style>"
        f"<h1>Glacier station melt dashboard</h1>{''.join(body)}"
    )
    print("\n".join(md))
    print(f"wrote {OUT / 'dashboard.md'} and {OUT / 'dashboard.html'}")


def cmd_station(args) -> None:
    app = start(args.target)
    print(table_md([dict(x) for x in app.station_days(args.station_id).collect()]))


def cmd_debug(args) -> None:
    app = start(args.target)
    r = app.Readings.table
    bad = r.where(r.melt_cm.errortype != None).select(  # noqa: E711
        r.station_id, r.day, err=r.melt_cm.errortype, msg=r.melt_cm.errormsg
    ).collect()
    print("rows whose melt_cm failed:")
    print(table_md([dict(x) for x in bad]))
    odd = r.where(r.n_ok == None).select(r.station_id, r.day, r.stakes).collect()  # noqa: E711
    print("readings whose stakes field is not a list (no usable stakes):")
    print(table_md([dict(x) for x in odd]))
    for cmd in (["pxt", "daemon", "status", "--json"], ["pxt", "health"], ["pxt", "status"]):
        print("$ " + " ".join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        print((p.stdout or p.stderr).strip())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="glacier")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    p = sub.add_parser("station")
    p.add_argument("station_id")
    p.set_defaults(fn=cmd_station)
    sub.add_parser("debug").set_defaults(fn=cmd_debug)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
