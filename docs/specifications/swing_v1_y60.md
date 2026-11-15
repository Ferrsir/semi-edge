# Specification: `swing_v1` R3 replication on unseen high-volatility stocks (Y60)

Status: **Specified (issue #180), before any result on Y60.** `swing_v1` selected R3 on U80
(2016-2024). R3 then passed the U80 holdout (2025-2026) and failed on X60, the 60 non-tech
stocks: -0.010R a trade at 10 bps, although it still beat its random-entry baseline there. By
`swing_v1`'s rule that is a FAIL, and it stays one.

This spec tests one explanation, written down before any Y60 bar was fetched. Every rule and
gate is fixed here (ground rule 3).

## Hypothesis

R3's bounce is a fraction of a stock's daily range, while the trading cost is a fixed 10 bps a
side. In volatile stocks the bounce is large against the cost. In steady large caps it is not.
If that is right, R3 should pass on volatile growth stocks it has never seen. If R3 fails on
them too, its U80 results are specific to that list and R3 is not reproducible.

This hypothesis was formed after seeing the X60 result. It is tested only on symbols that no
test in this project has read, which is what makes the test valid.

## What is tested

- **Rule:** R3 exactly as in `swing_v1.md`. Nothing is changed: no volatility filter, no new
  parameter, the same stop, exits, costs and portfolio rules. Regime index: QQQ.
- **Symbols (Y60):** 60 volatile US-listed growth stocks outside U80 and X60, written from
  memory before any of their bars was fetched:

```
ROKU PYPL ZM DOCU TWLO OKTA SPOT PINS SNAP RBLX U COIN HOOD SOFI AFRM UPST DKNG ABNB DASH LYFT
ETSY EBAY MELI SE BABA JD PDD BIDU NIO RIVN ENPH FSLR ISRG DXCM MRNA VRTX REGN ILMN ALGN TTD
HUBS VEEV BILL GTLB PATH ESTC CFLT S FTNT AKAM ADSK EA TTWO RDDT CVNA W CHWY Z EXPE MSTR
```

- **Period:** 2016-04-01..2026-10-23, one run. A symbol for which Alpaca returns no bars is
  dropped and named.
- **Command:** `semiedge backtest-swing --sample unseen-growth --candidates R3 --equity 5000 --risk-profile 5k`

## Gates (10 bps a side, the same as `swing_v1`'s unseen-symbol gates)

At least 300 study trades, mean R above 0, mean R without the five best trades above 0, MC1
lower bound above 0, MC2 lower bound above 0, positive $5,000 net (reset model), and MC3 with at
least 90% of account paths positive.

## What a result means

- **All gates met:** R3 has then passed its selection sample, a time holdout and an
  unseen-symbol replication inside its stated domain (volatile technology and growth stocks),
  and has failed outside it (X60). It becomes the candidate for a forward test on U80 plus Y60
  under frozen rules. It is still not evidence of a profitable system until forward results
  exist (ground rule 7), and its domain limit stays on the record.
- **Any gate missed:** R3 is not reproducible on unseen symbols. `swing_v1` ends as a FAIL with
  no candidate.

Also reported, not gates: results at 5 and 20 bps, the strict-halt run, drawdown, exposure,
QQQ bought and held, and the mean daily range (`ATR14 / C`) of the three universes, to show
whether the volatility explanation is even consistent with the data.

## Labels

Survivorship-biased list (many of these names fell 70% or more in 2022 and are still listed;
ones that were delisted are absent). Earnings blackout not applied. Price returns only.
Daily-bar stop fills.
