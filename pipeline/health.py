#!/usr/bin/env python3
"""MacroScanner — Phase 3: Damodaran industry health check.

For each GARP candidate in data/screen.json, compares key metrics (P/E,
PEG, ROE, net margin) against Damodaran's industry-level benchmarks, which
he updates annually from all US-listed companies.

Source: https://pages.stern.nyu.edu/~adamodar/pc/datasets/
Data is free, updated each January, hosted at NYU — no API key needed.
Works from GitHub Actions (not SEC-dependent).

Output: data/health.json with per-candidate vs-industry scores and a
sector concentration warning if ≥3 candidates cluster in one sector.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parents[1]
UA   = {"User-Agent": "MacroScanner/1.0"}

DAMO_BASE = "https://pages.stern.nyu.edu/~adamodar/pc/datasets"
DATASETS  = {
    "pe":     f"{DAMO_BASE}/pedata.xls",
    "roe":    f"{DAMO_BASE}/roe.xls",
    "margin": f"{DAMO_BASE}/margin.xls",
}

# Map Yahoo Finance broad sectors → Damodaran industry names to average over.
# Where multiple industries map to one sector, we weight by firm count.
SECTOR_MAP: dict[str, list[str]] = {
    "Information Technology": [
        "Software (System & Application)", "Software (Internet)",
        "Computer Services", "Semiconductor", "Semiconductor Equip",
        "Computers/Peripherals", "Electronics (General)",
    ],
    "Health Care": [
        "Healthcare Products", "Drugs (Pharmaceutical)", "Drugs (Biotechnology)",
        "Heathcare Information and Technology", "Hospitals/Healthcare Facilities",
        "Healthcare Support Services",
    ],
    "Financials": [
        "Banks (Regional)", "Bank (Money Center)",
        "Brokerage & Investment Banking", "Insurance (General)",
        "Financial Svcs. (Non-bank & Insurance)",
    ],
    "Consumer Discretionary": [
        "Retail (General)", "Retail (Special Lines)", "Recreation",
        "Hotel/Gaming", "Restaurant/Dining", "Auto & Truck", "Auto Parts",
    ],
    "Consumer Staples": [
        "Food Processing", "Beverage (Soft)", "Household Products",
        "Food Wholesalers", "Retail (Grocery and Food)",
    ],
    "Industrials": [
        "Machinery", "Aerospace/Defense", "Engineering/Construction",
        "Business & Consumer Services", "Transportation", "Trucking",
        "Transportation (Railroads)",
    ],
    "Energy": [
        "Oil/Gas (Production and Exploration)", "Oil/Gas (Integrated)",
        "Oilfield Svcs/Equip.", "Oil/Gas Distribution",
    ],
    "Materials": [
        "Metals & Mining", "Chemical (Basic)", "Chemical (Specialty)",
        "Paper/Forest Products", "Steel",
    ],
    "Communication Services": [
        "Telecom. Services", "Entertainment", "Software (Internet)",
        "Broadcasting", "Cable TV", "Publishing & Newspapers",
    ],
    "Utilities": [
        "Utility (General)", "Utility (Water)", "Power",
        "Green & Renewable Energy",
    ],
    "Real Estate": [
        "R.E.I.T.", "Real Estate (General/Diversified)",
        "Real Estate (Development)", "Real Estate (Operations & Services)",
        "Retail (REITs)",
    ],
}


def _fetch_xls(url: str) -> xlrd.book.Book:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return xlrd.open_workbook(file_contents=r.read())


def _header_row(ws: xlrd.sheet.Sheet) -> int:
    for i in range(ws.nrows):
        if str(ws.cell_value(i, 0)).strip().lower().startswith("industry"):
            return i
    raise RuntimeError(f"No header row found in sheet {ws.name}")


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def load_damodaran() -> dict[str, dict[str, float | None]]:
    """Return {industry_name: {forward_pe, peg, roe, net_margin, n_firms}}."""
    print("Fetching Damodaran datasets...", flush=True)
    pe_wb  = _fetch_xls(DATASETS["pe"])
    roe_wb = _fetch_xls(DATASETS["roe"])
    mar_wb = _fetch_xls(DATASETS["margin"])

    pe_ws  = pe_wb.sheet_by_name("Industry Averages")
    roe_ws = roe_wb.sheet_by_name("Industry Averages")
    mar_ws = mar_wb.sheet_by_name("Industry Averages")

    # PE sheet: col 0=Industry, 1=N firms, 5=Forward PE, 9=PEG
    pe_hr = _header_row(pe_ws)
    # ROE sheet: col 0=Industry, 1=N firms, 2=ROE unadjusted
    roe_hr = _header_row(roe_ws)
    # Margin sheet: col 0=Industry, 1=N firms, 3=Net Margin
    mar_hr = _header_row(mar_ws)

    # Build lookup by industry name
    roe_data = {
        str(roe_ws.cell_value(i, 0)).strip(): _safe_float(roe_ws.cell_value(i, 2))
        for i in range(roe_hr + 1, roe_ws.nrows)
        if roe_ws.cell_value(i, 0)
    }
    mar_data = {
        str(mar_ws.cell_value(i, 0)).strip(): _safe_float(mar_ws.cell_value(i, 3))
        for i in range(mar_hr + 1, mar_ws.nrows)
        if mar_ws.cell_value(i, 0)
    }

    industries: dict[str, dict] = {}
    for i in range(pe_hr + 1, pe_ws.nrows):
        name = str(pe_ws.cell_value(i, 0)).strip()
        if not name or name.lower().startswith("total"):
            continue
        n = _safe_float(pe_ws.cell_value(i, 1)) or 0
        industries[name] = {
            "n_firms":     int(n),
            "forward_pe":  _safe_float(pe_ws.cell_value(i, 5)),
            "peg":         _safe_float(pe_ws.cell_value(i, 9)),
            "roe":         roe_data.get(name),
            "net_margin":  mar_data.get(name),
        }

    print(f"  Loaded {len(industries)} Damodaran industries", flush=True)
    return industries


def sector_benchmarks(
    industries: dict[str, dict],
    sector: str,
) -> dict[str, float | None]:
    """Weighted average of Damodaran industries for the given Yahoo sector."""
    targets = SECTOR_MAP.get(sector, [])
    matched = [(n, industries[n]) for n in targets if n in industries]
    if not matched:
        return {}

    def wavg(key: str) -> float | None:
        pairs = [(d["n_firms"], d[key]) for _, d in matched
                 if d.get(key) is not None and d["n_firms"] > 0]
        if not pairs:
            return None
        total_n = sum(n for n, _ in pairs)
        return sum(n * v for n, v in pairs) / total_n if total_n else None

    return {
        "forward_pe":  wavg("forward_pe"),
        "peg":         wavg("peg"),
        "roe":         wavg("roe"),
        "net_margin":  wavg("net_margin"),
        "industries":  [n for n, _ in matched],
        "n_firms":     sum(d["n_firms"] for _, d in matched),
    }


def vs_industry(cand: dict, bench: dict) -> dict:
    """Return per-metric ratios: >1 = candidate is better than industry."""
    out = {}
    # PE: lower is better → industry/candidate
    if bench.get("forward_pe") and cand.get("pe") and cand["pe"] > 0:
        out["pe_vs_ind"] = round(bench["forward_pe"] / cand["pe"], 2)
    # PEG: lower is better → industry/candidate
    if bench.get("peg") and cand.get("peg") and cand["peg"] > 0:
        out["peg_vs_ind"] = round(bench["peg"] / cand["peg"], 2)
    # ROE: higher is better → candidate/industry
    if bench.get("roe") and cand.get("roe_pct") and cand["roe_pct"] is not None:
        ind_roe = bench["roe"] * 100  # Damodaran gives as decimal
        if ind_roe > 0:
            out["roe_vs_ind"] = round(cand["roe_pct"] / ind_roe, 2)
    # Net margin: higher is better (skip — screener doesn't capture margin directly)
    return out


def health_label(scores: dict) -> str:
    """Simple 3-level health label based on vs-industry ratios."""
    if not scores:
        return "unknown"
    vals = [v for v in scores.values() if isinstance(v, (int, float))]
    if not vals:
        return "unknown"
    above = sum(1 for v in vals if v > 1.1)
    below = sum(1 for v in vals if v < 0.8)
    if above >= 2:
        return "strong"   # candidate beats industry on 2+ metrics
    if below >= 2:
        return "weak"     # lags industry on 2+ metrics
    return "inline"       # roughly in line with peers


def main() -> None:
    screen_path = ROOT / "data" / "screen.json"
    if not screen_path.exists():
        print("ERROR: data/screen.json not found — run screener.py first",
              file=sys.stderr)
        sys.exit(1)

    screen = json.loads(screen_path.read_text(encoding="utf-8"))
    candidates = screen.get("candidates", [])
    if not candidates:
        print("No GARP candidates to health-check — writing empty health.json")
        out = {
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "No GARP candidates in screen.json",
            "candidates": [],
            "sector_concentration": [],
        }
        (ROOT / "data" / "health.json").write_text(
            json.dumps(out, indent=1), encoding="utf-8")
        return

    industries = load_damodaran()

    # Pre-compute benchmarks per sector (once per sector, not per candidate)
    sector_cache: dict[str, dict] = {}
    for c in candidates:
        sec = c.get("sector", "")
        if sec and sec not in sector_cache:
            sector_cache[sec] = sector_benchmarks(industries, sec)

    results = []
    for c in candidates:
        bench = sector_cache.get(c.get("sector", ""), {})
        scores = vs_industry(c, bench)
        label  = health_label(scores)
        results.append({
            "anon":   c["anon"],
            "ticker": c["ticker"],
            "sector": c["sector"],
            "health": label,
            "scores": scores,
            "benchmark": {k: round(v, 3) if isinstance(v, float) else v
                          for k, v in bench.items()
                          if k not in ("industries", "n_firms")},
        })
        print(f"  {c['anon']} ({c['sector'][:12]:12}) health={label:7}  "
              + "  ".join(f"{k}={v:.2f}x" for k, v in scores.items()))

    # Sector concentration: flag if ≥3 candidates in same sector
    from collections import Counter
    sector_counts = Counter(c["sector"] for c in candidates)
    concentration = [
        {"sector": sec, "count": n, "tickers": [c["ticker"] for c in candidates if c["sector"] == sec]}
        for sec, n in sector_counts.most_common()
        if n >= 3
    ]
    if concentration:
        alerts = [f"{x['sector']}={x['count']}" for x in concentration]
        print(f"  CONCENTRATION ALERT: {alerts}")

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "damodaran_url": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html",
        "note": ("vs-industry ratios >1.0 = candidate beats industry benchmark; "
                 "<1.0 = lags peers. Damodaran data is updated annually each January."),
        "candidates": results,
        "sector_concentration": concentration,
        "sector_benchmarks": {
            sec: {k: round(v, 3) if isinstance(v, float) else v
                  for k, v in bm.items() if k != "industries"}
            for sec, bm in sector_cache.items()
        },
    }
    dest = ROOT / "data" / "health.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    strong = sum(1 for r in results if r["health"] == "strong")
    print(f"OK  {strong}/{len(results)} strong vs industry -> {dest}")


if __name__ == "__main__":
    main()
