#!/usr/bin/env python3
"""MacroScanner — Telegram Broadcaster

Reads data/brief.json and pushes it to a specified Telegram Chat.
Runs as part of the automated weekly pipeline.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def escape_html(text: str) -> str:
    """Escape Telegram HTML special characters."""
    import html
    return html.escape(text)

def main() -> None:
    if not TOKEN or not CHAT_ID:
        print("WARN: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. Skipping push.", file=sys.stderr)
        return

    try:
        brief = json.loads((DATA_DIR / "brief.json").read_text(encoding="utf-8"))
        liq = json.loads((DATA_DIR / "liquidity.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        print(f"WARN: Missing data file for telegram push: {e}", file=sys.stderr)
        return

    # Format the message
    regime = liq.get("regime", {})
    label = regime.get("label", "Unknown")
    score = regime.get("score", 0)
    emoji = "🟢" if label == "Risk-On" else "🔴" if label == "Risk-Off" else "🟡"
    
    msg = f"<b>{escape_html(brief.get('headline', 'MacroScanner Weekly Update'))}</b>\n\n"
    msg += f"{emoji} <b>Regime:</b> {escape_html(label)} ({score:+d})\n\n"
    
    for para in brief.get("body", []):
        msg += f"{escape_html(para)}\n\n"
        
    if brief.get("watch"):
        msg += "<b>👀 Watch This Week:</b>\n"
        for w in brief["watch"]:
            msg += f"• {escape_html(w)}\n"
        msg += "\n"
        
    if brief.get("change_my_mind"):
        msg += "<b>🔄 What would flip the view:</b>\n"
        for w in brief["change_my_mind"]:
            msg += f"• {escape_html(w)}\n"
            
    # Send to Telegram
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    
    # Telegram max length is 4096. We split at 4000 to be safe.
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
    
    print(f"Pushing to Telegram in {len(chunks)} chunks...")
    try:
        for idx, chunk in enumerate(chunks):
            payload = {
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
                if resp.get("ok"):
                    print(f"OK pushed chunk {idx+1}/{len(chunks)} to Telegram successfully.")
                else:
                    print(f"ERR Telegram API returned: {resp}", file=sys.stderr)
    except urllib.error.HTTPError as e:
        print(f"ERR failed to push to Telegram: {e} - {e.read().decode('utf-8')}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERR failed to push to Telegram: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
