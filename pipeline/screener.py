#!/usr/bin/env python3
"""MacroScanner — Stage 2: GARP screener via Yahoo Finance.

No API key needed. Fetches P/E, PEG, EPS growth, revenue growth, ROE, D/E
via Yahoo Finance quoteSummary (one request per ticker). Writes data/screen.json
with anonymized candidate ids (Claude evaluates numbers before seeing names).

Why Yahoo Finance instead of SEC XBRL: GitHub Actions IPs are blocked by SEC
at the network level (403). Yahoo Finance has no such restriction.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
MIN_MCAP_B = 2.0

CRITERIA_DOC = {
    "growth_sweet": "EPS growth (trailing quarterly YoY) between 10 % and 40 % — fast but not frothy (Lynch sweet spot)",
    "peg_ok": "PEG ratio ≤ 1.5 — the primary gate; you're not paying up for growth",
    "rev_confirms": "Revenue growth ≥ 5 % — earnings growth backed by real sales",
    "roe_strong": "Return on equity ≥ 12 % — management generating returns above cost of capital",
    "debt_ok": "Debt/equity ≤ 1.0 (skipped for Financials where leverage is structural)",
}

YH_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = YH_UA
    retry = Retry(total=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


_sess = _session()


def _raw(d: dict | None, *keys):
    """Safe nested get into Yahoo Finance's {raw, fmt} dicts."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    if isinstance(d, dict):
        return d.get("raw")
    return d


def fundamentals(ticker: str) -> dict | None:
    sym = ticker.replace(".", "-")
    url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
           f"?modules=financialData,defaultKeyStatistics,summaryDetail,price")
    try:
        r = _sess.get(url, timeout=30)
        time.sleep(0.25)
        if r.status_code != 200:
            return None
        result = (r.json().get("quoteSummary") or {}).get("result") or []
        if not result:
            return None
        res = result[0]
        fd = res.get("financialData") or {}
        ks = res.get("defaultKeyStatistics") or {}
        sd = res.get("summaryDetail") or {}
        pr = res.get("price") or {}
        de_raw = _raw(fd, "debtToEquity")
        return {
            "price":      _raw(pr, "regularMarketPrice") or _raw(sd, "regularMarketPrice"),
            "mcap":       _raw(pr, "marketCap") or _raw(sd, "marketCap"),
            "pe":         _raw(sd, "trailingPE") or _raw(sd, "forwardPE"),
            "peg":        _raw(ks, "pegRatio"),
            "eps_growth": _raw(fd, "earningsGrowth"),  # trailing quarterly YoY
            "rev_growth": _raw(fd, "revenueGrowth"),
            "roe":        _raw(fd, "returnOnEquity"),
            # Yahoo reports D/E × 100 (e.g. 45.2 = 0.452); convert to ratio
            "de":         de_raw / 100 if de_raw is not None else None,
        }
    except Exception as e:
        print(f"  WARN {ticker}: {e}", file=sys.stderr)
        return None


def main() -> None:
    universe = list(csv.DictReader((ROOT / "data" / "universe.csv").open(encoding="utf-8")))
    print(f"Screening {len(universe)} tickers via Yahoo Finance...")

    candidates, no_data = [], 0
    for i, row in enumerate(universe):
        f = fundamentals(row["ticker"])
        if not f or f["price"] is None or f["peg"] is None:
            no_data += 1
            continue
        mcap_b = (f["mcap"] or 0) / 1e9
        if mcap_b < MIN_MCAP_B:
            continue
        peg = f["peg"]
        if peg is None or peg <= 0 or peg > 1.5:
            continue
        sector = row["sector"]
        fin = sector == "Financials"
        eps_g = (f["eps_growth"] or 0) * 100
        rev_g = (f["rev_growth"] or 0) * 100
        roe   = (f["roe"] or 0) * 100
        de    = f["de"]
        pe    = f["pe"]
        checks = {
            "growth_sweet": 10 <= eps_g <= 40,
            "peg_ok": peg <= 1.5,
            "rev_confirms": None if f["rev_growth"] is None else rev_g >= 5,
            "roe_strong": None if f["roe"] is None else roe >= 12,
            "debt_ok": None if (fin or de is None) else de <= 1.0,
        }
        score = sum(1 for v in checks.values() if v is True)
        candidates.append({
            "ticker": row["ticker"], "name": row["name"], "sector": sector,
            "mcap_b": round(mcap_b, 1),
            "price": round(f["price"], 2),
            "pe": round(pe, 1) if pe else None,
            "eps_growth_pct": round(eps_g, 1),
            "peg": round(peg, 2),
            "rev_growth_pct": round(rev_g, 1) if f["rev_growth"] is not None else None,
            "roe_pct": round(roe, 1) if f["roe"] is not None else None,
            "de": round(de, 2) if de is not None else None,
            "checks": checks, "score": score,
        })
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(universe)} done, {len(candidates)} passing PEG gate")

    candidates.sort(key=lambda c: (-c["score"], c["peg"]))
    candidates = candidates[:30]
    for i, c in enumerate(candidates, 1):
        c["anon"] = f"CAND-{i:02d}"

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance (quarterly trailing YoY metrics)",
        "criteria": CRITERIA_DOC,
        "coverage": {"universe": len(universe), "no_data": no_data, "candidates": len(candidates)},
        "candidates": candidates,
    }
    dest = ROOT / "data" / "screen.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"OK  {len(candidates)} GARP candidates -> {dest}")


if __name__ == "__main__":
    main()
