"""Render the organization profile star-history charts.

GitHub no longer serves per-star timestamps, so the curves are stitched together from:
  * profile/assets/stars/seed.json       exact curves up to 2026-11-02, recovered from the old chart
  * profile/assets/stars/snapshots.json  daily star counts recorded by this script
  * Party_OS assets/stars/history.json   daily counts of every Party OS project (summed into one curve)

Usage: python scripts/star_history.py [--offline] [--party-os-history PATH]
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "profile" / "assets"
SEED = ASSETS / "stars" / "seed.json"
SNAPSHOTS = ASSETS / "stars" / "snapshots.json"
PARTY_OS_HISTORY = "https://raw.githubusercontent.com/Roboparty/Party_OS/main/assets/stars/history.json"

TRACKED = ["Roboparty/RP1", "Roboparty/roboto_origin"]
PARTY_OS = "party_os_total"
SERIES = [  # legend order; drawn in reverse so the first entry ends up on top
    ("Roboparty/RP1", "#dd4528", {"en": "Roboparty/RP1", "zh": "Roboparty/RP1"}),
    (PARTY_OS, "#28a3dd", {"en": "Party OS (all projects)", "zh": "Party OS（全部项目）"}),
    ("Roboparty/roboto_origin", "#d4a017", {"en": "Roboparty/roboto_origin", "zh": "Roboparty/roboto_origin"}),
]
TEXT = {
    "en": {"x": "Time since first star (months)", "y": "GitHub Stars (log₂)", "mo": "mo",
           "dashed": "interpolated", "file": "star-history.svg"},
    "zh": {"x": "距首次 Star 时间（月）", "y": "GitHub Star 数（log₂）", "mo": "月",
           "dashed": "插值估计", "file": "star-history-zh.svg"},
}
MONTH = 30.4375
GAP_DAYS = 2

W, H = 900, 520
L, R, T, B = 78, 876, 28, 458
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def get_json(url: str):
    headers = {"User-Agent": "roboparty-star-history", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)


def midnight(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(), dt.timezone.utc)


def record_snapshot(snaps: dict, today: dt.date) -> None:
    snaps[today.isoformat()] = {r: get_json(f"https://api.github.com/repos/{r}")["stargazers_count"] for r in TRACKED}


def tracked_series(repo: str, seed: dict, snaps: dict) -> tuple[dt.datetime, list[tuple[float, float, bool]]]:
    """(first star, [(days since first star, stars, incoming segment interpolated)])."""
    s = seed["series"][repo]
    first = dt.datetime.fromisoformat(s["first_star"])
    pts = [(d, n, False) for d, n in s["points"]]
    as_of = dt.date.fromisoformat(seed["as_of"])
    for day in sorted(snaps):
        if repo not in snaps[day] or dt.date.fromisoformat(day) <= as_of:
            continue
        t = (midnight(dt.date.fromisoformat(day)) - first).total_seconds() / 86400
        pts.append((t, snaps[day][repo], t - pts[-1][0] > GAP_DAYS))
    return first, pts


def daily_curve(points: list[tuple[dt.date, float]], start: dt.date, n: int) -> list[float]:
    out = []
    for k in range(n):
        day = start + dt.timedelta(days=k)
        if day <= points[0][0]:
            out.append(float(points[0][1]))
            continue
        for (d0, c0), (d1, c1) in zip(points, points[1:]):
            if day <= d1:
                out.append(c0 + (c1 - c0) * (day - d0).days / max((d1 - d0).days, 1))
                break
        else:
            out.append(float(points[-1][1]))
    return out


def party_os_series(seed: dict, history: dict) -> tuple[list[tuple[float, float, bool]], int]:
    """Daily sum over all Party OS projects. Before the first snapshot each repository is either the
    recovered seed curve (up to seed["as_of"]) or a straight line from its creation date."""
    as_of = dt.date.fromisoformat(seed["as_of"])
    created = {r: dt.date.fromisoformat(d) for r, d in history["created"].items()}
    days = sorted(history["snapshots"])
    first_snapshot, last = dt.date.fromisoformat(days[0]), dt.date.fromisoformat(days[-1])
    start = min(created.values())
    n = (last - start).days + 1
    total = [0.0] * n
    for repo, made in created.items():
        snaps = [(dt.date.fromisoformat(d), history["snapshots"][d][repo]) for d in days if repo in history["snapshots"][d]]
        if repo in seed["series"]:
            s = seed["series"][repo]
            first = dt.datetime.fromisoformat(s["first_star"])
            times = [first + dt.timedelta(days=d) for d, _ in s["points"]]
            pts = []
            day = made
            while day <= as_of:
                pts.append((day, bisect.bisect_right(times, midnight(day))))
                day += dt.timedelta(days=1)
            pts += [(d, c) for d, c in snaps if d > as_of]
        else:
            pts = [(made, 0)] + [(d, c) for d, c in snaps if d > made]
        for k, v in enumerate(daily_curve(pts, start, n)):
            total[k] += v
    k0 = next(k for k, v in enumerate(total) if v >= 1)
    pts = [(float(k - k0), total[k], start + dt.timedelta(days=k) <= first_snapshot) for k in range(k0, n)]
    pts[0] = (0.0, pts[0][1], False)
    return pts, round(total[-1])


def star_label(n: int, lang: str) -> str:
    return f"{n // 1024}K★" if lang == "en" and n >= 1024 else f"{n}★"


def render(series: list[tuple[str, str, dict, list, int]], lang: str) -> str:
    tx = TEXT[lang]
    max_months = max(p[-1][0] for *_, p, _ in series) / MONTH
    top_months = 1 << max(4, math.ceil(math.log2(max(max_months, 1))))
    max_stars = max(max(v for _, v, _ in p) for *_, p, _ in series)
    top_exp = max(1, math.ceil(math.log2(max_stars)))
    kx = (R - L) / math.log1p(top_months)
    ky = (B - T) / top_exp

    def xy(t: float, n: float) -> tuple[float, float]:
        return L + kx * math.log1p(max(t, 0) / MONTH), B - ky * math.log2(max(n, 1))

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<g font-family="{FONT}" font-size="12" fill="#333">',
    ]
    for e in range(top_exp + 1):
        y = B - ky * e
        out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{R}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" fill="#555">{star_label(1 << e, lang)}</text>')
    m = 1
    while m <= top_months:
        x = L + kx * math.log1p(m)
        out.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{B}" stroke="#e5e7eb" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{B + 20}" text-anchor="middle" fill="#555">{m} {tx["mo"]}</text>')
        m *= 2
    cy = (T + B) / 2
    out += [
        f'<line x1="{L}" y1="{B}" x2="{R}" y2="{B}" stroke="#333" stroke-width="1.5"/>',
        f'<line x1="{L}" y1="{T}" x2="{L}" y2="{B}" stroke="#333" stroke-width="1.5"/>',
        f'<text x="{(L + R) / 2:.1f}" y="{B + 46}" text-anchor="middle" fill="#333">{tx["x"]}</text>',
        f'<text x="18" y="{cy:.1f}" text-anchor="middle" fill="#333" transform="rotate(-90 18 {cy:.1f})">{tx["y"]}</text>',
    ]
    for _, color, _, pts, _ in reversed(series):
        runs: list[tuple[bool, list[str]]] = []
        prev = None
        for i, (t, n, dashed) in enumerate(pts):
            p = "{:.1f},{:.1f}".format(*xy(t, n))
            if i == 0:
                runs.append((False, [p]))
            elif not runs or runs[-1][0] != dashed:
                runs.append((dashed, [prev, p]))
            elif p != runs[-1][1][-1]:
                runs[-1][1].append(p)
            prev = p
        for dashed, ps in runs:
            if len(ps) < 2:
                continue
            style = ' stroke-dasharray="5 5" stroke-opacity="0.75"' if dashed else ""
            out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" '
                       f'stroke-linecap="round"{style} points="{" ".join(ps)}"/>')
    lx, ly, lw = 621, 343, 245
    lh = 12 + 22 * (len(series) + 1)
    out.append(f'<rect x="{lx}" y="{ly}" width="{lw}" height="{lh}" fill="#ffffff" fill-opacity="0.92" stroke="#d1d5db" rx="6"/>')
    for i, (_, color, labels, _, count) in enumerate(series):
        y = ly + 9 + 22 * i
        out.append(f'<rect x="{lx + 12}" y="{y}" width="10" height="10" fill="{color}" rx="2"/>')
        out.append(f'<text x="{lx + 30}" y="{y + 9}" fill="#333">{labels[lang]}</text>')
        out.append(f'<text x="{lx + lw - 12}" y="{y + 9}" text-anchor="end" fill="#555">{count:,}</text>')
    y = ly + 9 + 22 * len(series)
    out.append(f'<line x1="{lx + 12}" y1="{y + 5}" x2="{lx + 34}" y2="{y + 5}" stroke="#888" stroke-width="2" stroke-dasharray="5 5"/>')
    out.append(f'<text x="{lx + 42}" y="{y + 9}" fill="#555">{tx["dashed"]}</text>')
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="do not record a new snapshot")
    ap.add_argument("--party-os-history", help="local copy of Party_OS assets/stars/history.json")
    args = ap.parse_args()

    seed = json.loads(SEED.read_text(encoding="utf-8"))
    snaps = json.loads(SNAPSHOTS.read_text(encoding="utf-8")) if SNAPSHOTS.exists() else {}
    if not args.offline:
        record_snapshot(snaps, dt.datetime.now(dt.timezone.utc).date())
        SNAPSHOTS.write_text(json.dumps(dict(sorted(snaps.items())), indent=1) + "\n", encoding="utf-8", newline="\n")
    if args.party_os_history:
        history = json.loads(Path(args.party_os_history).read_text(encoding="utf-8"))
    else:
        history = get_json(PARTY_OS_HISTORY)

    series = []
    for key, color, labels in SERIES:
        if key == PARTY_OS:
            pts, count = party_os_series(seed, history)
        else:
            _, pts = tracked_series(key, seed, snaps)
            count = pts[-1][1]
        series.append((key, color, labels, pts, count))
    for lang, tx in TEXT.items():
        (ASSETS / tx["file"]).write_text(render(series, lang), encoding="utf-8", newline="\n")
    print({key: count for key, *_, count in series})


if __name__ == "__main__":
    main()
