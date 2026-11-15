# Specification: `trend_v1` forward test (SIM ledger, no orders)

Status: **Specified (issue #180) on 2026-11-15, before any bar of the forward window exists.**
`trend_v1` failed its pre-registered test (`docs/results/trend_v1.md`). T1 is still the only
rule in this project whose $5,000 account made money on every sample it was run on. Backtests
cannot settle more than that. This spec freezes T1 and lets time test it.

Nothing here places, simulates at a broker, or authorizes an order. The ledger is the existing
backtest engine run on bars that arrive after the freeze.

## Frozen rule

T1 exactly as in `swing_v1.md` and engine `f0aa495`: weekly rotation into the three stocks with
the highest six-month momentum, regime `QQQ C > SMA200`, trend filter `C > SMA50`, 3-ATR
initial stop, 3-ATR trailing exit, 120-session time exit, entries and close-based exits at the
next open. The `5k` risk profile unchanged. 10 bps a side. Reset halt model, with every
drawdown halt listed in the ledger. No parameter may change during the forward test. A change
ends this test and starts a new one.

## Universe

The 140 symbols of U80 and Y60, the two lists on which T1 beat a random entry. It did not on
X60 (steady non-tech large caps), which is left out for that stated reason.

## Window

Starts with $5,000 in cash at the close of 2026-11-13, the last bar that existed when this was
written. The first entries are the signals of that close, filled at the open of 2026-11-16.
It runs through the last completed session in the cache each time the command is run.

## Command

```
semiedge data fetch --config-dir config/forward     # extend the cache (owner's paper data keys)
semiedge forward-trend --equity 5000 --risk-profile 5k
```

It prints the account's equity, every closed trade, the open positions with their stops, the
exits decided at the last close, and the entries planned for the next open. Because the rule is
frozen and the engine is deterministic, anyone can reproduce the ledger from the same cache.

## Reviews (fixed now)

T1 trades about 15 times a year, so forward evidence arrives slowly.

- **After 126 sessions (about 2027-05-17) and after 252 sessions (about 2027-11-15):** report
  the forward net result, maximum drawdown, trades, and QQQ over the same window, next to the
  backtest's range.
- **Stop early** if forward equity falls 15% below its start. The worst 5% of the Monte Carlo
  account paths on Y60 lost about 23% at some point over ten years, so a 15% loss inside one
  year would be outside what the backtest described.
- A forward result is judged against this question only: is the account above its start after
  252 sessions, with a maximum drawdown inside the backtest's Monte Carlo range? One year of a
  slow rule cannot prove an edge. It can show whether the backtest's picture was wrong.

## Use by the owner

The owner may mirror the planned entries by hand in the Alpaca paper account. That is the
owner's manual action. Agents do not place orders, and SEMI-EDGE's paper order plumbing (#21,
#162) is unchanged by this spec.
