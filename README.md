# 📡 Macro Scanner

Personal macro-thematic investment dashboard. Druckenmiller-inspired:
watch systemic liquidity first, fundamentals second, supply-chain
bottlenecks third. 1–3 year horizon. Phone-first, $0/month to run.

- **Robot:** GitHub Actions runs `pipeline/liquidity.py` every Monday
  (FRED data, no API key) → commits `data/liquidity.json`.
- **Brain:** a weekly Claude routine reads the fresh numbers and writes
  `data/brief.json` — the plain-English analysis.
- **Dashboard:** `index.html` on GitHub Pages.

Run locally: `python pipeline/liquidity.py && python -m http.server`

Personal research tool. Not financial advice.
