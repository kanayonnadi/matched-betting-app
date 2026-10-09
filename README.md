# Matched Betting Desk

A personal, local matched-betting **decision-support** application for Ontario,
Canada. It finds bookmaker/exchange opportunities, models executable STX depth
and fees, tracks promotions end-to-end, reconciles settlements, and manages
bankroll risk.

**This app never places wagers.** Every bet is placed manually by you. It never
automates logins, bypasses bookmaker controls, or scrapes account areas.

## Primary interface

- **Find a Bet** (default) — the promotion-first workflow:
  1. Paste a promotion (structured fallback fields available) and save it.
  2. Find a qualifying bet (ranked, depth-checked).
  3. Confirm the outcome and reward received.
  4. Find a conversion bet for the bonus token.
  5. See realized profit.
- **My Bets** — wagers grouped by promotion (qualifying, reward, conversion,
  realized profit, outstanding liabilities). Individual records are preserved.
- **Bankroll** — ledger with reserved vs available capital.

Everything else lives under **Advanced**: Calculator, Opportunities, Arbitrage,
Alerts, Bet Tracker, Offer Planner, Promotions (verification/lifecycle), Settlement
reconciliation, Risk/stress testing, Analytics, Dashboard.

## Data sources

- **The Odds API** (`ca` region): Canadian sportsbook BACK odds.
- **STX (Ontario)**: exchange LAY odds + order-book depth via signed REST/WS.

`DEMO MODE` uses deterministic mock data and can never produce an actionable
recommendation. `LIVE MODE` uses the configured adapters and **never falls back
to mock**; missing feeds surface an error. All money math uses `Decimal`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` (gitignored) with:

```
THE_ODDS_API_KEY=...
THE_ODDS_API_REGION=ca
THE_ODDS_API_BOOKMAKERS=playnow_ca,betmgm_ca_on,...
THE_ODDS_API_SPORTS=icehockey_nhl,basketball_nba,...

STX_KEY_ID=...
STX_PRIVATE_KEY_PATH=stx_prod.pem
STX_BASE_URL=https://stxapp.ca
STX_FEE_FACTOR=0.10
```

Run:

```bash
streamlit run app.py
```

## Workflow states

`DRAFT → QUALIFYING_FOUND → QUALIFYING_PLACED → QUALIFYING_SETTLED →
REWARD_RECEIVED → CONVERSION_FOUND → CONVERSION_PLACED → COMPLETED`, plus
`PARTIAL_HEDGE / VOIDED / CANCELLED / EXPIRED / INELIGIBLE`. States advance
automatically from recorded facts; the detailed audit stays in
`promotion_lifecycle_history`.

## Safety and reliability

- Fee-aware P&L; STX per-wager fee (`ceil` once per order).
- STX executable-depth checks; partial hedges show both outcome P&Ls and
  unhedged exposure (never the original qualifying loss).
- Bankroll reservations per account; sportsbook and exchange capital kept
  separate.
- Promotion verification requires complete terms + user-confirmed eligibility.
- Settlement reconciliation compares the matched predicted scenario to actuals.
- The fee model is labelled **EXTERNALLY_UNVERIFIED** until checked against real
  transactions.

## Tests

```bash
pytest -q
```

Calculations are tested independently of the implementation formulas where
practical.

## Limitations

- No automated promotion scraping; sources/terms are entered or fetched manually.
- Official promotion terms and STX fee behavior are not independently verified.
- Realized profit depends on manual settlement entry until real records exist.
- Not validated for real-money use; do not increase exposure based on test count.
