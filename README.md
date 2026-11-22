# CPR × Indicators × Exits: CE/PE intraday research

**This is a research runner, not an auto-trading bot. It does not place orders.**

## Run on GitHub (no PC/server required)
1. Create an empty **private** GitHub repository.
2. Upload all files preserving folders (including `.github/workflows/backtest.yml`).
3. Open **Actions** → **CPR combinatorial 5-minute backtest** → **Run workflow**.
4. Download the `cpr-combinations-and-trades` artifact from the completed workflow.
5. Read `results/summary.csv`, `results/shortlisted_exit_results.csv`, `results/trades.csv`, and `results/coverage.csv`.

The automatic weekday job is for incremental research, not live trading. GitHub scheduled jobs may run late or be skipped. For more stock symbols, edit `universe.csv` (example set = 5 candidates from each of 10 broad sectors, **not** verified top-five market-cap or a verified/current F&O constituents list). Historical constituency changes/survivorship bias matter.

## Critical data limitation
Yahoo 5-minute NSE price history is limited to approximately **the most recent 60 days**. GitHub cannot magically retrieve 5-year 5-minute data. The runner downloads recent 5-minute data, and optionally reads **real** previously archived 5-minute files placed at `data/5m/SYMBOL.csv`. To test 5 years, supply a licensed 5-year 5-minute OHLCV archive for each stock; the program checks and reports actual dates per symbol. Don't synthesize 5-minute candles from daily bars.

To provide data yourself, put e.g. `data/5m/HDFCBANK.csv` with columns `Datetime,Open,High,Low,Close,Volume`. Timestamps must be ISO8601 with timezone offset (e.g. `2025-01-02T09:15:00+05:30`) or naive times understood as **Asia/Kolkata**. These are **five-minute candle START timestamps**. Files can span more than 60 days. In `config.json`, set `source` to `csv` (only own archive), `hybrid` (merge archive plus Yahoo), or `yahoo` (Yahoo alone). Check `coverage.csv` for missing symbols.

## What the code is / isn't
- Replicates key **directional** concepts of the latest Pine strategy using numpy/pandas. It is **not a line-by-line verified TradingView-equivalent port**. Independent validation on the same data is required.
- EARLY: trigger = daily CPR boundary, EMA9/21 crossover or R1/PDH crossing. RETEST: bounce at EMA21/daily CPR/R1/PDH. STRONG: full original core filter alignment + breakout. RTP: 20/50 SMA aligned, parallel slope, break of preceding day-to-date high/low (no lookahead). Each entry can be assessed with different **additional** two-/three-indicator combinations.
- STRONG base is already a strict conjunction. Its additional two/three indicator combinations measure incremental filters, not replacement of strict STRONG. To study a truly stripped-down 2-filter setup, use EARLY or RTP base; see methodology.
- Indicators: Daily CPR, 9/21/200 EMA, ST(10,3), ST(7,2), RSI14, DMI/ADX14, daily R1/S1/PDH/PDL, session VWAP, SMA20/50/200, relative volume. Weekly/monthly CPR not yet implemented in this engine. No Camarilla/virgin-CPR entry in the first research version.
- Separate CE bullish / PE bearish **underlying directional signals**. Backtests buy/short the underlying stock as proxy. **No option premium, Greeks, IV, spread, expiry, strike or lot P&L is modelled.** 5% stop and 15% target are applied to **underlying price**; they are not option-premium risk or return, and a 15% move intraday is unlikely for many large caps. Do not treat results as CE/PE option win rates.
- Signal at completed 5-minute bar close; execution next bar open, with estimated costs/slippage. Entries 09:30 to 15:00 bar starts; flat by 15:15 close. If SL and TP both hit within same bar, assumes **stop first** (conservative). Gaps beyond stops fill at bar open; option margin/lot sizing is not simulated.
- One position per stock at a time; max 2 entries *per stock* per day, not portfolio-wide. For portfolio limits, add an across-stock event-level scheduler.

## Combinations and exits
Filters are paired/tripled (change `combination_sizes` to `[2,3,4]` for more). `exit_mode=shortlist` runs a **ST7,2 baseline** on every filter combination for each stock/base/side, then detailed exit comparison on top combinations. `exit_mode=full` runs **all defined exits for every combination** (can take many hours with 50 symbols). Report both train and held-out 30% test periods; minimum samples are configurable.

Exit variants in `backtest.py`: `ST7`, `SR`, `EMA9`, `SMA20`, `ST7_OR_SR`, `ST7_OR_EMA9`, `ST7_OR_SR_OR_EMA9`, `ST7_OR_SMA20`, `TIME_ONLY`, and `FIXED_5_15` — with optional 5% stop/15% target first-event wins. Every variant also squares off at 15:15. `ST7` uses ST(7,2) reversal; `SR` uses a frozen support/resistance invalidation reference captured at entry.

**Not all indicator combinations are independent** (e.g. EMA + SMA overlap). Multiple-testing bias is severe. `summary.csv` doesn't prove future performance. Reject low-sample combos; compare after costs and in truly unseen data. A win rate alone is not a ranking criterion.

## Local execution
```bash
pip install -r requirements.txt
python backtest.py --config config.json --universe universe.csv --output results
pytest -q
```

## Current research limitations
1. Yahoo has gaps/rate-limits. `coverage.csv` makes this explicit.
2. `yfinance` series may have adjustments/splits; verify data before use.
3. Historical member list / delisted stocks and corporate actions are not reconstructed.
4. 5-year five-minute archive is NOT included. There are no fabricated performance or ranking numbers.
5. Commissions in bps are only a simple underlying proxy, not Zerodha option charges, taxes or bid/ask slippage.
6. Train/test splits by calendar time, but repeated tuning can still overfit. Ideally add rolling walk-forward and cross-sector holdouts.
