# Specification — `momentum_v1`

Status: **Specified; implementation in progress (issue #12)**. Parameters: `config/strategies/momentum_v1.yaml`.

Long-only, sector-relative momentum breakout on completed **daily** bars.

## Hypothesis (to be tested, not assumed)

Semiconductor/AI-infrastructure stocks that break above their prior 20-session high on
above-average volume, while outperforming the sector ETF (SMH) and while the sector itself
is in an uptrend, continue to outperform over the following ≤ 5 sessions by more than
transaction costs. **No evidence supports this yet.** Phase 2 backtests, walk-forward
analysis and cost sensitivity exist to try to falsify it.

## Notation

For one symbol, sessions are indexed `t` (the signal session — the last **completed** bar).
`C_t, H_t, L_t, V_t` are close, high, low, volume. `M_t` is the SMH close.
All windows count sessions, not calendar days. "Prior window" means it ends at `t-1`.

| Name | Definition |
|---|---|
| `SMA_n(X)_t` | `mean(X_{t-n+1} … X_t)` — includes the current bar; undefined until `n` values exist |
| `PH_t` (prior high) | `max(H_{t-20} … H_{t-1})` — current bar **excluded** |
| `PV_t` (prior volume) | `mean(V_{t-20} … V_{t-1})` — current bar **excluded** |
| `R_t` (stock return) | `C_t / C_{t-20} − 1` |
| `RM_t` (SMH return) | `M_t / M_{t-20} − 1` (same session alignment as the stock) |
| `XR_t` (excess return) | `R_t − RM_t` |
| `RV_t` (relative volume) | `V_t / PV_t` |
| `TR_t` | `max(H_t − L_t, |H_t − C_{t-1}|, |L_t − C_{t-1}|)`; for the first bar `H_t − L_t` |
| `ATR_t` (Wilder, n=14) | first value at the n-th bar = `mean(TR_1 … TR_n)`; then `ATR_t = (ATR_{t-1}·(n−1) + TR_t) / n` |

## Mandatory conditions (ALL must hold)

| # | Condition | Formula |
|---|---|---|
| 1 | Sector regime | `M_t > SMA_50(M)_t` |
| 2 | Stock above trend | `C_t > SMA_20(C)_t` **and** `C_t > SMA_50(C)_t` |
| 3 | Breakout | `C_t > PH_t` |
| 4 | Relative strength | `XR_t ≥ 0.02` |
| 5 | Volume confirmation | `RV_t ≥ 1.2` |

Comparisons are strict where written `>` and inclusive where written `≥`.
If any input is undefined (insufficient history, NaN, missing session, stale data), the
symbol is **ineligible** with an explicit reason — never silently treated as passing.

Additional eligibility gates (data quality, fail closed):
- the symbol's last bar AND SMH's last bar are both the `as_of` session (fresh);
- no missing sessions in the lookback window (`max_missing_sessions_in_lookback`, default 0);
- stock and SMH series share the same data feed (never mix IEX with SIP);
- the symbol is `role: tradable` (benchmarks are never candidates).

## Ranking score (0–100)

Each component is a linear ramp clipped to `[0, 1]`:

```
clip(x) = min(1, max(0, x))

s_RS = clip( (XR_t − 0.02) / (S_RS − 0.02) )            S_RS = relative_strength_saturation = 0.15
s_BO = clip( ((C_t − PH_t) / ATR_t) / S_BO )            S_BO = breakout_atr_saturation     = 1.0
s_RV = clip( (RV_t − 1.2) / (S_RV − 1.2) )              S_RV = relative_volume_saturation  = 3.0
s_TP = (# of sessions u in [t−19, t] with C_u > SMA_50(C)_u) / 20     (trend_persistence_window = 20)

score = 100 · (0.40·s_RS + 0.30·s_BO + 0.20·s_RV + 0.10·s_TP)
```

- Thresholds (0.02, 1.2) and saturations come from config; the formulas above use defaults.
- An eligible candidate scores in `[0, 100]`. A just-qualifying candidate can score near 0
  on the first three components by construction.
- The score is computed whenever all inputs are defined (also for ineligible symbols, for
  display), but **only eligible symbols are ranked**. Ties break by symbol ascending.
- **The weights and saturation points are heuristic priors. There is no statistical
  evidence for them.** Phase 2 sensitivity analysis must report how results change with them.

## Proposed trade parameters (scanner output; not orders)

```
entry_reference = C_t                       (actual entry is the NEXT session open — backtest/execution)
stop            = C_t − 2 · ATR_t           (stop_atr_multiple)
target          = C_t + 4 · ATR_t           (target_atr_multiple)
risk_per_share  = entry_reference − stop
shares_risk     = floor( equity · max_risk_per_trade_pct / risk_per_share )
shares_alloc    = floor( equity · max_position_pct / entry_reference )
shares          = min(shares_risk, shares_alloc)   (0 if any input invalid or risk_per_share ≤ 0)
```

Portfolio-level limits (gross exposure, max positions, circuit breakers, earnings blackout,
pending orders) are enforced by the Phase 3 risk manager, not the scanner. The scanner labels
its size as *proposed* and states which equity figure it used.

## No-look-ahead rules

1. Strategies receive only completed bars: sessions `≤ as_of` where `as_of` is the last session
   whose close + `bar_settle_minutes` has passed.
2. Prior-window baselines (`PH`, `PV`) exclude bar `t` (implemented as `shift(1)` then rolling).
3. A vectorised feature frame must satisfy: for every `t`, recomputing features on the data
   truncated at `t` yields identical values at `t` (tested by truncation tests).
4. Entries in backtests occur no earlier than the open of session `t+1`.

## Split adjustment

Daily bars are split-adjusted (`adjustment: split`). All conditions are ratios of prices or
volumes within one series, so they are invariant to the constant rescaling a split adjustment
applies. Dividend adjustment is not applied (documented in DECISIONS.md D-007).
