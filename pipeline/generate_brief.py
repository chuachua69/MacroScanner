#!/usr/bin/env python3
"""MacroScanner — Automated Brain

Uses Anthropic API (Claude 3.5 Sonnet) to read liquidity.json, 
screen.json, and funds.json, and write the weekly brief.json.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

API_URL = "https://api.anthropic.com/v1/messages"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

PROMPT = """You are the MacroScanner Brain. Your job is to read the weekly data from the robots and write the plain-English weekly brief. 

Iron Rules:
1. Teach, don't lecture. Explain jargon. Owner is NOT a finance professional.
2. Long-term signals only (1-3 year horizon).
3. Anti-hype rule: when discussing screen entrants, evaluate the anonymized fundamental metrics BEFORE looking at the ticker.
4. Output STRICTLY in the requested JSON schema. Do not output markdown code blocks. Just raw JSON.

Here is the data for this week:

=== LIQUIDITY REGIME ===
{liquidity}

=== GARP SCREENER ===
{screen}

=== 13F FUNDS (SMART MONEY) ===
{funds}

Based on this data, write the weekly brief. Address:
- What changed this week in the liquidity regime and rationale.
- NEW screen entrants worth a look (focus on strong fundamentals).
- Notable smart-money moves or cross-fund conviction overlap.

Output EXACTLY this JSON schema:
{{
  "headline": "A short, catchy, newspaper-style headline (e.g. 'Credit stress cracks 6% as liquidity drains')",
  "body": [
    "Paragraph 1 explaining the regime change or continuation...",
    "Paragraph 2 explaining what the smart money is doing and the screener highlights..."
  ],
  "watch": [
    "2 to 4 things to watch this week, short bullet points"
  ],
  "change_my_mind": [
    "1 to 3 things that would flip the current regime call"
  ]
}}"""

def main() -> None:
    if not API_KEY:
        print("WARN: ANTHROPIC_API_KEY not set. Skipping automated brief generation.", file=sys.stderr)
        return

    # Load data
    try:
        liq = json.loads((DATA_DIR / "liquidity.json").read_text(encoding="utf-8"))
        screen = json.loads((DATA_DIR / "screen.json").read_text(encoding="utf-8"))
        funds = json.loads((DATA_DIR / "funds.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        print(f"WARN: Missing data file for brief generation: {e}", file=sys.stderr)
        return

    # Truncate screen and funds slightly to save tokens if they are huge, though they should be small
    req_body = {
        "model": "claude-3-5-sonnet-20240620",
        "max_tokens": 1500,
        "temperature": 0.5,
        "messages": [
            {
                "role": "user",
                "content": PROMPT.format(
                    liquidity=json.dumps(liq.get("regime"), indent=2),
                    screen=json.dumps(screen.get("candidates", [])[:20], indent=2), # top 20
                    funds=json.dumps([f for f in funds.get("funds", []) if f.get("new_buys")], indent=2)
                )
            }
        ]
    }

    req = urllib.request.Request(
        API_URL, 
        data=json.dumps(req_body).encode("utf-8"), 
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )

    print("Generating brief via Anthropic API...")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
            
        content = resp["content"][0]["text"].strip()
        # Clean up any potential markdown code blocks Claude might add despite instructions
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
            
        brief = json.loads(content.strip())
        
        # Add metadata
        brief["written_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        brief["week_of"] = liq.get("week_of", "unknown")
        
        out = DATA_DIR / "brief.json"
        out.write_text(json.dumps(brief, indent=2), encoding="utf-8")
        print(f"OK generated brief -> {out}")
        
    except Exception as e:
        print(f"ERR failed to generate brief: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
