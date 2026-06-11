#!/usr/bin/env python3
"""Build data/universe.csv — the screening universe.

S&P 500 constituents (community-maintained dataset, includes CIK) plus a short
hand-picked list of major US-listed foreign names. Re-run occasionally; the
weekly screener just reads the committed CSV.
"""
from __future__ import annotations

import csv
import io
import urllib.request
from pathlib import Path

SP500_CSV = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
             "main/data/constituents.csv")
HEADERS = {"User-Agent": "MacroScanner personal research"}

# Major foreign companies trading on US exchanges. CIK left blank — the
# screener resolves missing CIKs from SEC's ticker map at runtime. Many file
# IFRS (20-F) and won't appear in us-gaap data; they still matter for 13F overlap.
ADRS = [
    ("TSM", "Taiwan Semiconductor (ADR)", "Information Technology"),
    ("ASML", "ASML Holding (ADR)", "Information Technology"),
    ("ARM", "Arm Holdings (ADR)", "Information Technology"),
    ("SAP", "SAP SE (ADR)", "Information Technology"),
    ("NVO", "Novo Nordisk (ADR)", "Health Care"),
    ("AZN", "AstraZeneca (ADR)", "Health Care"),
    ("TM", "Toyota Motor (ADR)", "Consumer Discretionary"),
    ("SONY", "Sony Group (ADR)", "Consumer Discretionary"),
    ("MELI", "MercadoLibre", "Consumer Discretionary"),
    ("SE", "Sea Limited (ADR)", "Communication Services"),
    ("SHOP", "Shopify", "Information Technology"),
    ("NU", "Nu Holdings", "Financials"),
]


def main() -> None:
    req = urllib.request.Request(SP500_CSV, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) < 400:
        raise RuntimeError(f"S&P 500 source looks wrong: only {len(rows)} rows")

    out = Path(__file__).resolve().parents[1] / "data" / "universe.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "name", "sector", "cik", "source"])
        for r in rows:
            w.writerow([r["Symbol"], r["Security"], r["GICS Sector"],
                        r.get("CIK", "").strip(), "sp500"])
        for t, n, s in ADRS:
            w.writerow([t, n, s, "", "adr"])
    print(f"OK  {len(rows)} S&P 500 + {len(ADRS)} ADRs -> {out}")


if __name__ == "__main__":
    main()
