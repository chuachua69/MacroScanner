#!/usr/bin/env python3
"""MacroScanner — Historical Backtest Generator

Pulls 10 years of SPY data (Yahoo Finance) and FRED data, 
computes the Liquidity Regime score for each week historically, 
and outputs data/backtest.json for the frontend chart.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Import the logic and thresholds from liquidity.py
import liquidity

ROOT = Path(__file__).resolve().parents[1]

# 10 years = 520 weeks. We fetch a bit more for the 13w lookback
YEARS = 10
LOOKBACK_W = liquidity.LOOKBACK_W

def fetch_spy(start_date: date) -> list[tuple[date, float]]:
    """Fetch daily adjusted close for SPY via Yahoo Finance."""
    period1 = int(datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.now(timezone.utc).timestamp())
    
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/SPY?period1={period1}&period2={period2}&interval=1d&events=history"
    req = urllib.request.Request(url, headers={"User-Agent": liquidity.HEADERS["User-Agent"]})
    
    out: list[tuple[date, float]] = []
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
            
        res = data.get("chart", {}).get("result", [])
        if not res: return []
        
        timestamps = res[0].get("timestamp", [])
        adjclose = res[0].get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", [])
        
        for ts, px in zip(timestamps, adjclose):
            if px is not None:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                out.append((dt, float(px)))
    except Exception as e:
        print(f"WARN failed to fetch SPY: {e}", file=sys.stderr)
        
    return out

def get_closest_px(obs: list[tuple[date, float]], target: date) -> float | None:
    """Return the price on or just before the target date."""
    best_px = None
    for d, px in obs:
        if d > target:
            break
        best_px = px
    return best_px

def main() -> None:
    today = date.today()
    last_wed = today - timedelta(days=(today.weekday() - 2) % 7)
    
    n_weeks = (YEARS * 52)
    start_date = last_wed - timedelta(weeks=n_weeks + LOOKBACK_W + 4)
    
    print(f"Fetching data since {start_date}...")
    
    # Fetch SPY
    spy_data = fetch_spy(start_date)
    if not spy_data:
        raise RuntimeError("Failed to fetch SPY data for backtest.")
        
    # Fetch FRED
    raw: dict[str, list[tuple[date, float]]] = {}
    for sid in ("WALCL", "RRPONTSYD", "WTREGEN", "BAMLH0A0HYM2", "DTWEXBGS", "BAA10Y"):
        raw[sid] = liquidity.fetch(sid, start_date)
        time.sleep(0.5) # Be nice to FRED CSV
        
    # Build grid
    grid = [last_wed - timedelta(weeks=i) for i in range(n_weeks)][::-1]
    
    # Project FRED onto a daily-resolution grid so we can index accurately 
    # instead of just the weekly grid. Wait, `on_grid` does exactly what we need for the `grid`.
    # Let's project FRED data onto an expanded grid that covers grid[0] - 13 weeks.
    expanded_grid = [grid[0] - timedelta(weeks=i) for i in range(LOOKBACK_W, 0, -1)] + grid
    
    series: dict[str, list[float | None]] = {}
    for sid in ("WALCL", "RRPONTSYD", "WTREGEN", "BAMLH0A0HYM2", "DTWEXBGS", "BAA10Y"):
        scale = liquidity.TO_TRILLIONS.get(sid, 1.0)
        series[sid] = [v * scale if v is not None else None 
                       for v in liquidity.on_grid(raw[sid], expanded_grid)]

    # Compute Net Liquidity
    net = [w - r - t if None not in (w, r, t) else None
           for w, r, t in zip(series["WALCL"], series["RRPONTSYD"], series["WTREGEN"])]
           
    # Evaluate score for each week in the target `grid`
    # expanded_grid has length `n_weeks + LOOKBACK_W`
    # grid has length `n_weeks`
    # So index i in `grid` corresponds to index `i + LOOKBACK_W` in expanded_grid.
    
    history_out = []
    
    for i, d in enumerate(grid):
        idx = i + LOOKBACK_W
        
        # Current values
        net_now = net[idx]
        hy_now = series["BAMLH0A0HYM2"][idx]
        if hy_now is None and series["BAA10Y"][idx] is not None:
            hy_now = series["BAA10Y"][idx] * 2.0
            
        usd_now = series["DTWEXBGS"][idx]
        
        # T-13 values
        net_then = net[idx - LOOKBACK_W]
        hy_then = series["BAMLH0A0HYM2"][idx - LOOKBACK_W]
        if hy_then is None and series["BAA10Y"][idx - LOOKBACK_W] is not None:
            hy_then = series["BAA10Y"][idx - LOOKBACK_W] * 2.0
            
        usd_then = series["DTWEXBGS"][idx - LOOKBACK_W]
        
        # SPY Price
        spy_px = get_closest_px(spy_data, d)
        
        if None in (net_now, hy_now, usd_now, net_then, hy_then, usd_then, spy_px):
            continue
            
        liq_pct = liquidity.pct(net_now, net_then)
        hy_chg = hy_now - hy_then
        usd_pct = liquidity.pct(usd_now, usd_then)
        
        liq_sig = liquidity.signal(liq_pct, bullish_above=True, thr=liquidity.LIQ_PCT_THR)
        hy_sig = liquidity.signal(hy_chg, bullish_below=True, thr=liquidity.HY_PP_THR)
        usd_sig = liquidity.signal(usd_pct, bullish_below=True, thr=liquidity.USD_PCT_THR)
        
        score = 2 * liq_sig + hy_sig + usd_sig
        crisis = hy_now >= liquidity.HY_CRISIS_LEVEL
        
        if crisis or score <= -2:
            label = "Risk-Off"
        elif score >= 2:
            label = "Risk-On"
        else:
            label = "Neutral"
            
        history_out.append({
            "date": d.isoformat(),
            "spy": round(spy_px, 2),
            "score": score,
            "regime": label
        })
        
    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "years": YEARS,
        "data": history_out
    }
    
    dest = ROOT / "data" / "backtest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"OK generated {len(history_out)} backtest weeks -> {dest}")

if __name__ == "__main__":
    main()
