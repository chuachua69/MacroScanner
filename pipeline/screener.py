#!/usr/bin/env python3
"""MacroScanner — Stage 2: GARP screener (Peter Lynch criteria).

Fundamentals come from SEC XBRL "frames" (one request per metric-period for
EVERY US filer at once — ~30 requests total, no API key). Prices from Yahoo's
public chart endpoint. Writes data/screen.json with anonymized candidate ids
(Claude evaluates numbers before seeing names — anti-hype rule).

TTM math: TTM = annual(Y-1) + quarters(Y, 1..n) - quarters(Y-1, 1..n).
Foreign IFRS filers (most ADRs) are absent from us-gaap frames and are
counted in coverage stats rather than scored.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEC_UA = {"User-Agent": "MacroScanner personal research chuachua69@users.noreply.github.com"}
YH_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
FRAMES = "https://data.sec.gov/api/xbrl/frames/{tax}/{tag}/{unit}/{frame}.json"

MIN_MCAP_B = 2.0
CRITERIA_DOC = {
    "growth_sweet": "EPS growth (TTM vs prior TTM) between 10% and 40% — fast but sustainable (Lynch)",
    "peg_ok": "PEG (P/E divided by EPS growth) at or below 1.5",
    "rev_confirms": "Revenue growth at least 5% — earnings growth backed by real sales",
    "roe_strong": "Return on equity at least 12%",
    "debt_ok": "Long-term debt / equity at or below 1.0 (skipped for Financials)",
}


def fetch_json(url: str, ua: dict, tries: int = 3) -> dict | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=ua)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2.0 * (i + 1))
        except Exception:
            time.sleep(2.0 * (i + 1))
    return None


def frame_map(tax: str, tag: str, unit: str, frame: str) -> dict[int, float]:
    """One XBRL frame -> {cik: value}."""
    data = fetch_json(FRAMES.format(tax=tax, tag=tag, unit=unit, frame=frame), SEC_UA)
    time.sleep(0.15)  # SEC fair-access pacing
    if not data:
        print(f"  frame missing: {tag} {frame}", file=sys.stderr)
        return {}
    return {row["cik"]: row["val"] for row in data.get("data", [])}


def merged_frame(tags: list[tuple[str, str, str]], frame: str) -> dict[int, float]:
    """Try tags in order; later tags only fill ciks the earlier ones missed."""
    out: dict[int, float] = {}
    for tax, tag, unit in tags:
        for cik, val in frame_map(tax, tag, unit, frame).items():
            out.setdefault(cik, val)
    return out


def latest_quarter(today: date) -> tuple[int, int]:
    """Most recent calendar quarter whose 10-Qs are filed (~50 days after end)."""
    y, q = today.year, (today.month - 1) // 3  # previous quarter index
    if q == 0:
        y, q = y - 1, 4
    qend = date(y, q * 3, 28)
    if (today - qend).days < 50:
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return y, q


def ttm_maps(tags: list[tuple[str, str, str]], y: int, n: int
             ) -> tuple[dict[int, float], dict[int, float]]:
    """Return ({cik: TTM}, {cik: prior-year TTM}) for a duration metric."""
    ann = {yy: merged_frame(tags, f"CY{yy}") for yy in (y - 1, y - 2)}
    qtr = {(yy, qq): merged_frame(tags, f"CY{yy}Q{qq}")
           for yy in (y, y - 1, y - 2) for qq in range(1, n + 1)}

    def build(base_year: int, cur_year: int) -> dict[int, float]:
        out = {}
        for cik, base in ann[base_year].items():
            cur = [qtr[(cur_year, qq)].get(cik) for qq in range(1, n + 1)]
            old = [qtr[(base_year, qq)].get(cik) for qq in range(1, n + 1)]
            if None not in cur and None not in old:
                out[cik] = base + sum(cur) - sum(old)
        return out

    return build(y - 1, y), build(y - 2, y - 1)


def instant_map(tags: list[tuple[str, str, str]], y: int, n: int) -> dict[int, float]:
    """Instant metric at quarter end; previous quarter fills gaps."""
    out = merged_frame(tags, f"CY{y}Q{n}I")
    py, pn = (y, n - 1) if n > 1 else (y - 1, 4)
    for cik, val in merged_frame(tags, f"CY{py}Q{pn}I").items():
        out.setdefault(cik, val)
    return out


def yahoo_price(ticker: str) -> float | None:
    sym = ticker.replace(".", "-")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
    data = fetch_json(url, YH_UA, tries=2)
    time.sleep(0.12)
    try:
        return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except (TypeError, KeyError, IndexError):
        return None


def main() -> None:
    universe = list(csv.DictReader((ROOT / "data" / "universe.csv").open(encoding="utf-8")))

    # resolve missing CIKs from SEC's ticker map
    tickmap = fetch_json("https://www.sec.gov/files/company_tickers.json", SEC_UA) or {}
    by_ticker = {v["ticker"].upper(): int(v["cik_str"]) for v in tickmap.values()}
    for row in universe:
        row["cik_i"] = int(row["cik"]) if row["cik"].strip() else by_ticker.get(
            row["ticker"].upper().replace(".", "-")) or by_ticker.get(row["ticker"].upper())

    y, n = latest_quarter(date.today())
    print(f"Screening as of CY{y}Q{n} | universe {len(universe)}")

    eps_ttm, eps_old = ttm_maps([("us-gaap", "EarningsPerShareDiluted", "USD-per-shares")], y, n)
    rev_tags = [("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
                ("us-gaap", "Revenues", "USD")]
    rev_ttm, rev_old = ttm_maps(rev_tags, y, n)
    ni_ttm, _ = ttm_maps([("us-gaap", "NetIncomeLoss", "USD")], y, n)
    equity = instant_map([("us-gaap", "StockholdersEquity", "USD")], y, n)
    ltd = instant_map([("us-gaap", "LongTermDebtNoncurrent", "USD"),
                       ("us-gaap", "LongTermDebt", "USD")], y, n)
    shares = instant_map([("dei", "EntityCommonStockSharesOutstanding", "shares")], y, n)

    candidates, no_data, growth_fail = [], 0, 0
    pre = []
    for row in universe:
        cik = row["cik_i"]
        if not cik or cik not in eps_ttm or cik not in eps_old:
            no_data += 1
            continue
        e1, e0 = eps_ttm[cik], eps_old[cik]
        if e1 <= 0 or e0 <= 0:
            growth_fail += 1
            continue
        g = (e1 - e0) / abs(e0) * 100
        if not (5 <= g <= 60):  # wide pre-filter; strict band is a criterion
            growth_fail += 1
            continue
        pre.append((row, cik, e1, g))

    print(f"pre-filter survivors: {len(pre)} (fetching prices...)")
    for row, cik, e1, g in pre:
        price = yahoo_price(row["ticker"])
        if price is None:
            continue
        sh = shares.get(cik)
        mcap_b = price * sh / 1e9 if sh else None
        if mcap_b is not None and mcap_b < MIN_MCAP_B:
            continue
        pe = price / e1
        peg = pe / g if g > 0 else None
        r1, r0 = rev_ttm.get(cik), rev_old.get(cik)
        rev_g = (r1 - r0) / abs(r0) * 100 if r1 and r0 else None
        eq = equity.get(cik)
        roe = ni_ttm[cik] / eq * 100 if cik in ni_ttm and eq and eq > 0 else None
        de = ltd.get(cik, 0.0) / eq if eq and eq > 0 else None
        fin = row["sector"] == "Financials"

        checks = {
            "growth_sweet": 10 <= g <= 40,
            "peg_ok": peg is not None and peg <= 1.5,
            "rev_confirms": None if rev_g is None else rev_g >= 5,
            "roe_strong": None if roe is None else roe >= 12,
            "debt_ok": None if (fin or de is None) else de <= 1.0,
        }
        score = sum(1 for v in checks.values() if v is True)
        if not checks["peg_ok"]:
            continue
        candidates.append({
            "ticker": row["ticker"], "name": row["name"], "sector": row["sector"],
            "mcap_b": round(mcap_b, 1) if mcap_b else None,
            "price": round(price, 2), "pe": round(pe, 1),
            "eps_growth_pct": round(g, 1), "peg": round(peg, 2),
            "rev_growth_pct": round(rev_g, 1) if rev_g is not None else None,
            "roe_pct": round(roe, 1) if roe is not None else None,
            "de": round(de, 2) if de is not None else None,
            "checks": {k: v for k, v in checks.items()}, "score": score,
        })

    candidates.sort(key=lambda c: (-c["score"], c["peg"]))
    candidates = candidates[:30]
    for i, c in enumerate(candidates, 1):
        c["anon"] = f"CAND-{i:02d}"

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "quarter": f"CY{y}Q{n}",
        "criteria": CRITERIA_DOC,
        "coverage": {"universe": len(universe), "no_usgaap_data": no_data,
                     "outside_growth_band": growth_fail,
                     "priced_and_screened": len(pre), "candidates": len(candidates)},
        "candidates": candidates,
    }
    dest = ROOT / "data" / "screen.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"OK  {len(candidates)} GARP candidates -> {dest}")


if __name__ == "__main__":
    main()
