# MacroScanner — project brain file

Personal macro-thematic investment scanner (Druckenmiller-inspired, 1–3 year
horizon). Owner is NOT a finance professional — all analysis output must be
plain English, jargon explained. **Not a product; never add auth/payments.**

## Iron rules
1. **Math in Python, reasoning in Claude.** Never let an LLM compute numbers;
   never let Python pretend to judge. `pipeline/` is stdlib-only, no pip deps.
2. **No broker connections, ever.** This tool analyses; the owner trades manually.
3. **Long-term signals only.** No intraday/technical indicators. Weekly cadence.
4. **Free data only** unless owner explicitly approves a paid source.

## Layout
- `pipeline/liquidity.py` — Stage 1 robot. FRED → Net Liquidity + regime score
  → `data/liquidity.json`. Run: `python pipeline/liquidity.py`
- `pipeline/screener.py` — Stage 2 robot. SEC XBRL frames (fundamentals) +
  Yahoo chart API (prices) → Lynch GARP screen → `data/screen.json`.
- `pipeline/funds.py` — Stage 2b robot. EDGAR 13Fs for 11 tracked funds →
  top holdings, new buys, cross-fund overlap → `data/funds.json`. CIKs are
  name-validated at runtime; ok=false means CIK mismatch, fix the FUNDS table.
- `pipeline/build_universe.py` — regenerates `data/universe.csv` (S&P 500 +
  ADR shortlist). Run occasionally, not weekly.
- `index.html` — static phone-first dashboard (GitHub Pages serves repo root).
- `data/*.json` — robot outputs, committed weekly by Actions.
- `data/brief.json` — Claude-written plain-English weekly brief. Schema:
  `{written_utc, week_of, headline, body[], watch[], change_my_mind[]}`
- `.github/workflows/weekly.yml` — Monday cron + manual + on pipeline push.
- NOTE: SEC blocks this home IP sometimes (rate-threshold page). The pipeline
  is designed to run in GitHub Actions; don't debug SEC 403s locally.

## Weekly Claude routine (the "thinking half")
After the robot refreshes data: read `data/liquidity.json`, `data/screen.json`
and `data/funds.json`, then write a NEW `data/brief.json` — what changed this
week, regime rationale, NEW screen entrants worth a look (evaluate the
anonymized metrics BEFORE looking at the ticker — anti-hype rule), notable
smart-money moves/overlap, 2–4 things to watch, what would flip the regime
call. Teach, don't lecture. Commit as `brief: week of YYYY-MM-DD` and push.

## Roadmap
- [x] Phase 1 — liquidity monitor + dashboard
- [x] Phase 2 — GARP screener (Lynch criteria, anonymized scoring) + 13F tracker
- [x] Phase 3 — Damodaran health check (pedata/roe/margin XLS → vs-industry ratios + sector concentration alert)
- [x] Phase 4 — Positions tab: favouriting, Kelly/¼-Kelly sizing, price levels, macro calendar, sector-grouped screener with composite R/R ranking

## 13F watch list (Phase 2) — free via SEC EDGAR
- **Tier 1 (conviction):** Duquesne Family Office (Druckenmiller), Appaloosa
  (Tepper), Pershing Square, Third Point, Greenlight, Baupost, Lone Pine, Tiger Global
- **Tier 2 (early growth signal):** Coatue, Altimeter, Dragoneer
- **Tier 3 (mainstream check):** Capital Group, Fidelity — LOOKUP ONLY at
  deep-dive time ("is it mainstream yet?"), never scanned as a feed
- 13Fs lag 45 days, long-only US equities. Confirmation signal, never copy-trade.
