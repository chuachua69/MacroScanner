#!/usr/bin/env python3
"""MacroScanner — Stage 2b: 13F tracker for hand-picked funds.

Pulls the two most recent 13F-HR filings per fund straight from SEC EDGAR
(free), computes top holdings by weight, new buys and exits, and cross-fund
conviction overlap (by CUSIP). Writes data/funds.json.

CIKs are validated at runtime: the fund's official SEC name must contain the
expected keyword, otherwise the fund is flagged ok=false and skipped — a
wrong CIK can never silently produce garbage.

Caveats baked into the output: 13Fs lag up to 45 days after quarter end and
show only US long equity positions. Confirmation signal, never copy-trade.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UA = {"User-Agent": "MacroScanner personal research chuachua69@users.noreply.github.com"}

# (display name, tier, CIK, keyword that must appear in the SEC entity name)
FUNDS = [
    ("Duquesne Family Office (Druckenmiller)", 1, 1536411, "DUQUESNE"),
    ("Appaloosa (Tepper)", 1, 1656456, "APPALOOSA"),
    ("Pershing Square (Ackman)", 1, 1336528, "PERSHING SQUARE"),
    ("Third Point (Loeb)", 1, 1040273, "THIRD POINT"),
    ("Greenlight Capital (Einhorn)", 1, 1079114, "GREENLIGHT"),
    ("Baupost Group (Klarman)", 1, 1061768, "BAUPOST"),
    ("Lone Pine Capital", 1, 1061165, "LONE PINE"),
    ("Tiger Global", 1, 1167483, "TIGER GLOBAL"),
    ("Coatue Management", 2, 1135730, "COATUE"),
    ("Altimeter Capital", 2, 1541617, "ALTIMETER"),
    ("Dragoneer Investment Group", 2, 1602119, "DRAGONEER"),
]


def get(url: str, tries: int = 3) -> bytes | None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                time.sleep(0.15)
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2.0 * (i + 1))
        except Exception:
            time.sleep(2.0 * (i + 1))
    return None


def get_json(url: str) -> dict | None:
    raw = get(url)
    return json.loads(raw) if raw else None


def latest_13fs(cik: int, count: int = 2) -> tuple[str, list[dict]]:
    """Return (sec_entity_name, latest `count` 13F filings, newest first)."""
    sub = get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if not sub:
        return "", []
    rec = sub.get("filings", {}).get("recent", {})
    rows = [
        {"acc": rec["accessionNumber"][i], "form": rec["form"][i],
         "filed": rec["filingDate"][i], "period": rec["reportDate"][i]}
        for i in range(len(rec.get("form", [])))
        if rec["form"][i] in ("13F-HR", "13F-HR/A")
    ]
    # latest filing per report period (amendments supersede originals)
    by_period: dict[str, dict] = {}
    for r in rows:
        if r["period"] not in by_period or r["filed"] > by_period[r["period"]]["filed"]:
            by_period[r["period"]] = r
    ordered = sorted(by_period.values(), key=lambda r: r["period"], reverse=True)
    return sub.get("name", ""), ordered[:count]


def holdings(cik: int, acc: str) -> list[dict]:
    """Parse the information-table XML of one 13F filing -> aggregated holdings."""
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    idx = get_json(f"{folder}/index.json")
    if not idx:
        return []
    xmls = [it for it in idx.get("directory", {}).get("item", [])
            if it["name"].lower().endswith(".xml")
            and "primary_doc" not in it["name"].lower()]
    if not xmls:
        return []
    xmls.sort(key=lambda it: int(it.get("size") or 0), reverse=True)
    raw = get(f"{folder}/{xmls[0]['name']}")
    if not raw:
        return []
    # strip XML namespaces so tag lookups are simple
    text = re.sub(rb'xmlns(:\w+)?="[^"]*"', b"", raw)
    text = re.sub(rb"<(/?)\w+:", rb"<\1", text)
    root = ET.fromstring(text)

    agg: dict[tuple[str, str], dict] = {}
    for it in root.iter("infoTable"):
        issuer = (it.findtext("nameOfIssuer") or "?").strip()
        cusip = (it.findtext("cusip") or "?").strip().upper()
        value = float(it.findtext("value") or 0)
        sh = float(it.findtext("shrsOrPrnAmt/sshPrnamt") or 0)
        pc = (it.findtext("putCall") or "").strip().lower()
        key = (cusip, pc)
        if key not in agg:
            agg[key] = {"issuer": issuer, "cusip": cusip, "put_call": pc,
                        "value": 0.0, "shares": 0.0}
        agg[key]["value"] += value
        agg[key]["shares"] += sh
    return list(agg.values())


def main() -> None:
    funds_out, latest_holdings = [], {}
    for name, tier, cik, keyword in FUNDS:
        sec_name, filings = latest_13fs(cik)
        if keyword not in sec_name.upper():
            print(f"WARNING: CIK {cik} resolved to '{sec_name}', expected "
                  f"'{keyword}' — skipping {name}", file=sys.stderr)
            funds_out.append({"name": name, "tier": tier, "cik": cik, "ok": False,
                              "note": f"CIK mismatch: SEC says '{sec_name}'"})
            continue
        if not filings:
            funds_out.append({"name": name, "tier": tier, "cik": cik, "ok": False,
                              "note": "no 13F-HR filings found"})
            continue

        cur = holdings(cik, filings[0]["acc"])
        prev = holdings(cik, filings[1]["acc"]) if len(filings) > 1 else []
        total = sum(h["value"] for h in cur) or 1.0
        cur.sort(key=lambda h: h["value"], reverse=True)
        prev_by_key = {(h["cusip"], h["put_call"]): h for h in prev}
        prev_keys = set(prev_by_key)
        cur_keys = {(h["cusip"], h["put_call"]) for h in cur}

        def label(h):
            return h["issuer"] + (f" ({h['put_call']}s)" if h["put_call"] else "")

        top = [{"issuer": label(h), "pct": round(h["value"] / total * 100, 1)}
               for h in cur[:10]]
        new = [label(h) for h in cur if (h["cusip"], h["put_call"]) not in prev_keys]
        exited = [label(h) for k, h in prev_by_key.items() if k not in cur_keys]
        adds = []
        for h in cur:
            p = prev_by_key.get((h["cusip"], h["put_call"]))
            if p and p["shares"] > 0 and h["shares"] > p["shares"] * 1.25:
                adds.append({"issuer": label(h),
                             "shares_chg_pct": round((h["shares"] / p["shares"] - 1) * 100)})
        adds.sort(key=lambda a: -a["shares_chg_pct"])

        funds_out.append({
            "name": name, "tier": tier, "cik": cik, "ok": True,
            "sec_name": sec_name, "period": filings[0]["period"],
            "filed": filings[0]["filed"], "positions": len(cur),
            "portfolio_b": round(total / 1e9, 2),
            "top": top, "new_buys": new[:12], "exited": exited[:12],
            "big_adds": adds[:8],
        })
        latest_holdings[name] = {(h["cusip"]): h["issuer"] for h in cur[:50]
                                 if not h["put_call"]}
        print(f"OK  {name}: {len(cur)} positions, ${total/1e9:.1f}B "
              f"(period {filings[0]['period']})")

    # conviction overlap: same CUSIP in the top-50 of 3+ tracked funds
    seen: dict[str, dict] = {}
    for fund, hmap in latest_holdings.items():
        for cusip, issuer in hmap.items():
            entry = seen.setdefault(cusip, {"issuer": issuer, "funds": []})
            entry["funds"].append(fund)
    overlap = sorted(
        [{"issuer": v["issuer"], "n": len(v["funds"]), "funds": v["funds"]}
         for v in seen.values() if len(v["funds"]) >= 3],
        key=lambda o: -o["n"])

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "13Fs lag up to 45 days; US long equities only. Confirmation, not copy-trade.",
        "funds": funds_out,
        "overlap": overlap[:20],
    }
    dest = ROOT / "data" / "funds.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    ok = sum(1 for f in funds_out if f.get("ok"))
    print(f"OK  {ok}/{len(FUNDS)} funds tracked, {len(overlap)} overlap names -> {dest}")


if __name__ == "__main__":
    main()
