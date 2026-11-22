# CPR Combinatorial 5-minute Research — Dhan ONLY

Data: DhanHQ v2 historical intraday `POST /charts/intraday`, **NSE_EQ/EQUITY** (underlying). No Yahoo, no trades/orders.

## GitHub
1. Upload all files, including `.github/workflows/backtest.yml`, to a **private** GitHub repository.
2. Settings > Secrets and variables > Actions > New repository secret:
   - `DHAN_CLIENT_ID` (your Dhan client ID)
   - `DHAN_ACCESS_TOKEN` (your valid Dhan access token)
3. Actions > **CPR Dhan 5Y combinatorial research** > Run workflow.
4. First do a smoke test with `max_stocks=3`; then run with `max_stocks=0` for all 50.
5. Download artifact `dhan-cpr-5year-results`, inspect coverage.csv first.

## Important data assumptions
- 5 years 5-minute history is advertised for **active** instruments by Dhan. API limits each request to 90 calendar days. This project downloads in 80-day chunks.
- Universe has 50 representative stocks from different sectors; it has NOT been independently verified to be the top-five current F&O names per sector.
- Security IDs are looked up live from Dhan's published NSE_EQ instrument master. Renamed/delisted symbols fail explicitly, never silently replaced.
- Actual retrieved coverage is shown in coverage.csv; do not assume all symbols have 5 years.
- CE/PE are *underlying bullish/bearish directional signals*, not real option premiums. 5% SL / 15% target are **underlying percentages**. Results do not measure option contract P&L or actual premium decay.
- The existing EARLY/STRONG/RETEST/RTP formulas are approximate research implementations, not Pine-for-Pine perfect clones.
- Model selection over many combinations is susceptible to overfitting. Evaluate sample size and truly out-of-sample results.
- Previous project contained intrabar simulation simplifications and a ST7 technical implementation requiring additional independent validation; results are **research-only**, not a basis for automatic/live orders.
- This workflow is manual, not scheduled, since 5-years × 50 names × many strategy variants is computationally intensive.

Dhan official docs: https://dhanhq.co/docs/v2/historical-data/ and https://dhanhq.co/docs/v2/instruments/
