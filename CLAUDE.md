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
- `pipeline/liquidity.py` — Stage 1 robot. Fetches FRED, computes Net Liquidity
  + regime score, writes `data/liquidity.json`. Run: `python pipeline/liquidity.py`
- `index.html` — static phone-first dashboard (GitHub Pages serves repo root).
- `data/liquidity.json` — robot output (committed; refreshed by Actions weekly).
- `data/brief.json` — Claude-written plain-English weekly brief. Schema:
  `{written_utc, week_of, headline, body[], watch[], change_my_mind[]}`
- `.github/workflows/weekly.yml` — Monday cron + manual trigger.

## Weekly Claude routine (the "thinking half")
After the robot refreshes data: read `data/liquidity.json`, write a NEW
`data/brief.json` — what changed this week, what it means, regime rationale,
2–4 things to watch, what would flip the regime call. Teach, don't lecture.
Commit as `brief: week of YYYY-MM-DD`.

## Roadmap
- [x] Phase 1 — liquidity monitor + dashboard
- [ ] Phase 2 — GARP screener (Lynch criteria, anonymized scoring) + 13F tracker
- [ ] Phase 3 — Damodaran health check + supply-chain bottleneck tracing (10-Ks)
- [ ] Phase 4 — guardrails: pilot-trade checklist, position-sizing discipline

## 13F watch list (Phase 2) — free via SEC EDGAR
- **Tier 1 (conviction):** Duquesne Family Office (Druckenmiller), Appaloosa
  (Tepper), Pershing Square, Third Point, Greenlight, Baupost, Lone Pine, Tiger Global
- **Tier 2 (early growth signal):** Coatue, Altimeter, Dragoneer
- **Tier 3 (mainstream check):** Capital Group, Fidelity — LOOKUP ONLY at
  deep-dive time ("is it mainstream yet?"), never scanned as a feed
- 13Fs lag 45 days, long-only US equities. Confirmation signal, never copy-trade.
