#!/usr/bin/env python3
"""MacroScanner — Stage 1: systemic liquidity monitor.

Pulls public FRED data (fredgraph.csv endpoint — no API key required),
computes US Net Liquidity and a fully deterministic regime score, and
writes data/liquidity.json for the static dashboard.

Design rule: ALL math lives here, never in the LLM. Stdlib only — no pip.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
FRED_API = ("https://api.stlouisfed.org/fred/series/observations"
            "?series_id={sid}&observation_start={start}&api_key={key}"
            "&file_type=json&sort_order=asc")
HEADERS = {"User-Agent": "Mozilla/5.0 MacroScanner/0.1 (personal research tool)"}
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()

WEEKS_SHOWN = 104  # ~2 years of weekly history for the dashboard sparklines
LOOKBACK_W = 13    # regime trend window = one quarter

# FRED series and their native units (used to normalise to trillions of $).
# WALCL      Fed total assets, weekly Wednesday level, MILLIONS of $
# RRPONTSYD  Overnight reverse repo, daily, BILLIONS of $
# WTREGEN    Treasury General Account, weekly average, MILLIONS of $
# BAMLH0A0HYM2  High-yield option-adjusted spread, daily, PERCENT
# DTWEXBGS   Nominal broad US dollar index, daily, INDEX
# T10Y2Y     10yr minus 2yr Treasury spread, daily, PERCENT
TO_TRILLIONS = {"WALCL": 1e-6, "RRPONTSYD": 1e-3, "WTREGEN": 1e-6}

# Loose sanity ranges in trillions — catch unit mistakes, not market moves.
SANITY_T = {"WALCL": (3, 12), "RRPONTSYD": (-0.01, 3), "WTREGEN": (0.02, 1.6)}

# Regime score thresholds (documented on the dashboard methodology card).
LIQ_PCT_THR = 0.5   # net liquidity 13w % change: above +0.5 bullish, below -0.5 bearish
HY_PP_THR = 0.3     # HY spread 13w change in points: -0.3 bullish, +0.3 bearish
USD_PCT_THR = 1.0   # dollar 13w % change: -1 bullish (easier), +1 bearish (tighter)
HY_CRISIS_LEVEL = 6.0  # HY OAS above this forces Risk-Off regardless of score


def fetch(sid: str, start: date) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    if FRED_API_KEY:
        url = FRED_API.format(sid=sid, start=start.isoformat(), key=FRED_API_KEY)
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        for obs in data.get("observations", []):
            if obs["value"] not in (".", ""):
                out.append((date.fromisoformat(obs["date"]), float(obs["value"])))
    else:
        # fallback: web CSV (works locally; may time out from cloud CI without key)
        url = FRED_CSV.format(sid=sid, start=start.isoformat())
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=90) as resp:
            text = resp.read().decode("utf-8-sig")
        for row in list(csv.reader(io.StringIO(text)))[1:]:
            if len(row) >= 2 and row[1] not in (".", ""):
                out.append((date.fromisoformat(row[0]), float(row[1])))
    if not out:
        raise RuntimeError(
            f"FRED returned no observations for {sid}. "
            + ("" if FRED_API_KEY else "Set FRED_API_KEY env var for reliable CI access.")
        )
    return out


def on_grid(obs: list[tuple[date, float]], grid: list[date],
            max_gap_days: int = 21) -> list[float | None]:
    """Forward-fill observations onto the weekly grid; None if data is stale."""
    vals: list[float | None] = []
    i, last = 0, None
    for d in grid:
        while i < len(obs) and obs[i][0] <= d:
            last = obs[i]
            i += 1
        vals.append(last[1] if last and (d - last[0]).days <= max_gap_days else None)
    return vals


def pct(now: float, then: float) -> float:
    return (now - then) / then * 100.0


def signal(value: float, bullish_below: float | None = None,
           bullish_above: float | None = None, thr: float = 0.0) -> int:
    """+1 / 0 / -1 around a symmetric threshold."""
    if bullish_above is not None:
        return 1 if value >= thr else (-1 if value <= -thr else 0)
    return 1 if value <= -thr else (-1 if value >= thr else 0)


def history(grid: list[date], vals: list[float | None], digits: int) -> list[dict]:
    return [{"d": d.isoformat(), "v": round(v, digits)}
            for d, v in zip(grid, vals) if v is not None][-WEEKS_SHOWN:]


def main() -> None:
    today = date.today()
    last_wed = today - timedelta(days=(today.weekday() - 2) % 7)
    n_weeks = WEEKS_SHOWN + LOOKBACK_W + 1
    grid = [last_wed - timedelta(weeks=i) for i in range(n_weeks)][::-1]
    start = grid[0] - timedelta(days=30)

    raw: dict[str, list[tuple[date, float]]] = {}
    series: dict[str, list[float | None]] = {}
    for sid in ("WALCL", "RRPONTSYD", "WTREGEN", "BAMLH0A0HYM2", "DTWEXBGS", "T10Y2Y"):
        raw[sid] = fetch(sid, start)
        scale = TO_TRILLIONS.get(sid, 1.0)
        series[sid] = [v * scale if v is not None else None
                       for v in on_grid(raw[sid], grid)]
        if sid in SANITY_T:
            lo, hi = SANITY_T[sid]
            latest = series[sid][-1]
            if latest is None or not (lo <= latest <= hi):
                print(f"WARNING: {sid} latest={latest} outside sane range "
                      f"({lo}-{hi} $T) — check units!", file=sys.stderr)

    net = [w - r - t if None not in (w, r, t) else None
           for w, r, t in zip(series["WALCL"], series["RRPONTSYD"], series["WTREGEN"])]

    for name, vals in [("net liquidity", net), ("HY OAS", series["BAMLH0A0HYM2"]),
                       ("dollar", series["DTWEXBGS"])]:
        if vals[-1] is None or vals[-1 - LOOKBACK_W] is None:
            raise RuntimeError(f"Missing data for {name} at scoring points")

    liq_pct = pct(net[-1], net[-1 - LOOKBACK_W])
    hy_now = series["BAMLH0A0HYM2"][-1]
    hy_chg = hy_now - series["BAMLH0A0HYM2"][-1 - LOOKBACK_W]
    usd_pct = pct(series["DTWEXBGS"][-1], series["DTWEXBGS"][-1 - LOOKBACK_W])

    liq_sig = signal(liq_pct, bullish_above=True, thr=LIQ_PCT_THR)
    hy_sig = signal(hy_chg, bullish_below=True, thr=HY_PP_THR)
    usd_sig = signal(usd_pct, bullish_below=True, thr=USD_PCT_THR)

    score = 2 * liq_sig + hy_sig + usd_sig
    crisis = hy_now >= HY_CRISIS_LEVEL
    if crisis or score <= -2:
        label, sizing = "Risk-Off", "Watchlist only — no new pilot positions, build cash."
    elif score >= 2:
        label, sizing = "Risk-On", "Full pilot positions allowed (e.g. 3–5% starters)."
    else:
        label, sizing = "Neutral", "Half-size pilots; demand stronger fundamentals."

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week_of": last_wed.isoformat(),
        "as_of": {sid: obs[-1][0].isoformat() for sid, obs in raw.items()},
        "regime": {
            "label": label,
            "score": score,
            "crisis_override": crisis,
            "sizing_hint": sizing,
            "components": [
                {"id": "net_liquidity", "name": "Net Liquidity trend (13w)",
                 "value": round(liq_pct, 2), "unit": "%", "signal": liq_sig,
                 "weight": 2, "contribution": 2 * liq_sig},
                {"id": "hy_oas", "name": "Credit spread trend (13w)",
                 "value": round(hy_chg, 2), "unit": "pp", "signal": hy_sig,
                 "weight": 1, "contribution": hy_sig},
                {"id": "dollar", "name": "US Dollar trend (13w)",
                 "value": round(usd_pct, 2), "unit": "%", "signal": usd_sig,
                 "weight": 1, "contribution": usd_sig},
            ],
        },
        "metrics": {
            "net_liquidity": {"name": "Net Liquidity", "unit": "$T",
                              "latest": round(net[-1], 3),
                              "change_13w_pct": round(liq_pct, 2),
                              "history": history(grid, net, 3)},
            "walcl": {"name": "Fed Balance Sheet", "unit": "$T",
                      "latest": round(series["WALCL"][-1], 3),
                      "history": history(grid, series["WALCL"], 3)},
            "rrp": {"name": "Reverse Repo (RRP)", "unit": "$T",
                    "latest": round(series["RRPONTSYD"][-1], 3),
                    "history": history(grid, series["RRPONTSYD"], 3)},
            "tga": {"name": "Treasury General Account", "unit": "$T",
                    "latest": round(series["WTREGEN"][-1], 3),
                    "history": history(grid, series["WTREGEN"], 3)},
            "hy_oas": {"name": "High-Yield Credit Spread", "unit": "%",
                       "latest": round(hy_now, 2),
                       "change_13w_pp": round(hy_chg, 2),
                       "history": history(grid, series["BAMLH0A0HYM2"], 2)},
            "dollar": {"name": "Broad Dollar Index", "unit": "",
                       "latest": round(series["DTWEXBGS"][-1], 2),
                       "change_13w_pct": round(usd_pct, 2),
                       "history": history(grid, series["DTWEXBGS"], 2)},
            "yield_curve": {"name": "Yield Curve (10y−2y)", "unit": "%",
                            "latest": round(series["T10Y2Y"][-1], 2),
                            "history": history(grid, series["T10Y2Y"], 2)},
        },
        "thresholds": {"liq_pct": LIQ_PCT_THR, "hy_pp": HY_PP_THR,
                       "usd_pct": USD_PCT_THR, "hy_crisis": HY_CRISIS_LEVEL,
                       "lookback_weeks": LOOKBACK_W},
    }

    dest = Path(__file__).resolve().parents[1] / "data" / "liquidity.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"OK  {label} (score {score:+d})  net liquidity ${net[-1]:.3f}T "
          f"({liq_pct:+.2f}% / 13w)  ->  {dest}")


if __name__ == "__main__":
    main()
