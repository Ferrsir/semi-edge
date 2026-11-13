# SEMI-EDGE operator runbook

PAPER trading only. Live trading is prohibited in code, config and policy. Nothing in this
runbook places an order: no command on `main` submits, changes or cancels orders, and the
order lifecycle (#21), reconciliation (#22), position management (#23) and the trading loop
(#24) are not built yet. Sections marked *(after PR #N)* describe commands that exist only once
that PR is merged.

## 1. Install

You need Python 3.12 or newer, git, and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Ferrsir/semi-edge.git
cd semi-edge
python -m pip install --user uv      # if uv is not installed yet
uv sync --locked                     # behind a TLS-intercepting proxy: uv sync --locked --system-certs
uv run semiedge version
```

If `uv` is not on PATH on Windows, use `python -m uv` in place of `uv` (for example
`python -m uv sync --locked`), or call the tools in `.venv/Scripts/` directly.

## 2. Set up `.env`

Secrets come only from environment variables or a local `.env` file in the repository root.
`.env` is git-ignored; never commit it, paste it into an issue, or show it to an agent
(`.claude/settings.json` denies agents read access to it). The repository is public.

```dotenv
# Alpaca PAPER keys only (paper dashboard -> API keys). Live keys must never be used.
ALPACA_PAPER_API_KEY=
ALPACA_PAPER_SECRET_KEY=

# Earnings calendar for the R16 blackout (#20). Free-tier keys are enough.
FINNHUB_API_KEY=
ALPHAVANTAGE_API_KEY=

# Safety flags. These are the defaults; leave them as they are.
TRADING_MODE=scanner
ENABLE_ORDER_SUBMISSION=false
ALLOW_LIVE_TRADING=false
```

`TRADING_MODE` accepts `scanner`, `backtest` or `paper`. `live` exists only as a name and is
refused at startup, as is `ALLOW_LIVE_TRADING=true`. Optional settings: `SEMIEDGE_CONFIG_DIR`
(default `config`), `SEMIEDGE_DATA_DIR` (default `data`), `SEMIEDGE_LOG_LEVEL`, and
`SEMIEDGE_LOG_FORMAT` (`json` or `text`).

Check the result without printing any secret:

```powershell
uv run semiedge config check
```

If a key is ever exposed, revoke it in the vendor's dashboard first, then replace it in `.env`.

## 3. Run the tests

Every test runs offline. No test needs or receives credentials.

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run bandit -r src scripts -ll -q
```

Useful subsets are `uv run pytest -m safety` (trading-safety invariants) and
`uv run pytest -m integration` (adapters with mocked network and temporary storage). The one
opt-in live smoke test (`tests/integration/test_paper_account_live.py`) runs only when you set
`SEMIEDGE_PAPER_SMOKE=1` and real PAPER keys.

## 4. Market data and the earnings calendar

```powershell
uv run semiedge data fetch            # completed daily bars from Alpaca into data/ (needs PAPER keys)
uv run semiedge data status           # cache summary and freshness per symbol
```

The feed comes from `config/app.yaml` (`data.daily_feed`, currently `sip`). Only Alpaca data
feeds SEMIEDGE. Yahoo and other unofficial feeds are excluded (AGENTS.md rule 6).

*(After PR #88)* The earnings calendar combines Finnhub (primary) and Alpha Vantage (cross-check):

```powershell
uv run semiedge earnings refresh      # at most one successful fetch per source per UTC day
uv run semiedge earnings status       # per-symbol R16 view from the cache; no network
```

`status` exits non-zero when any tradable symbol is blocked, and prints the reason (for
example `finnhub_stale`, `sources_disagree`). To unblock one symbol after checking its date on the
company's investor-relations page, add an entry to `config/earnings_overrides.yaml` (format in
the file's header). An entry expires after `override_max_age_days` (`config/earnings.yaml`) or once
its date has passed. Free tiers allow 25 Alpha Vantage requests per day and 60 Finnhub
requests per minute. The refresh limits in `config/earnings.yaml` stay well under both.

## 5. Scanner and read-only paper account

```powershell
uv run semiedge scan                       # ranked momentum_v1 report from the cache
uv run semiedge scan --source synthetic    # labelled synthetic data, for a dry run without keys
uv run semiedge account                    # read-only PAPER account snapshot (needs PAPER keys)
uv run semiedge backtest --start 2024-01-02 --end 2024-06-28 --source synthetic
```

A scan or backtest is research output. A backtest number may be quoted only together with the
command that reproduces it, and nothing here is evidence of a trading edge.

## 6. Dashboards

The project status dashboard (issue #47) is a static page with no market data and no controls.
Open `project-status/dist/index.html` in a browser. To refresh its snapshot from the board and
GitHub (needs an authenticated `gh`):

```powershell
cd project-status
python scripts/refresh-snapshot.py --repo ..
npm.cmd ci --ignore-scripts
npm.cmd test
```

*(After PR #82)* The trading dashboard (issue #25) is a read-only Streamlit app bound to
`127.0.0.1`:

```powershell
uv sync --locked --extra dashboard
uv run semiedge dashboard --source synthetic   # or --source cache
```

Its only write is a manual halt on the Operations page (type `HALT`, an actor and a reason). A
halt stops new entries. Exits and protective stops continue.

## 7. Paper sessions

A paper trading session in which SEMIEDGE places orders on its own does not exist yet. Today a
"paper session" means running the scanner and the read-only account snapshot by hand (sections 4
and 5). Keep `ENABLE_ORDER_SUBMISSION=false`.

Even after the trading loop is built, R16 rejects every new entry until
`semiedge earnings status` shows the tradable symbols as `VERIFIED` (both vendors fresh and in
agreement, or a fresh override). This section will gain start and stop steps when #24 lands.

## 8. Stop everything safely

Nothing in SEMIEDGE runs in the background or on a schedule: every command above runs in the
foreground and exits on its own.

1. Press `Ctrl+C` in any terminal running `semiedge`, Streamlit or a test run.
2. *(After PR #82)* To block new entries while state is kept, request a manual halt from the
   dashboard first. Clearing a halt is a CLI-only action (`semiedge halt clear`, planned in
   #21).
3. Agent CLIs (Claude Code, Codex) also run only in the terminal tabs you open. Close those tabs
   to stop them. No scheduled or overnight agent exists unless you set one up.
4. Open paper positions or orders, if you placed any by hand, stay with Alpaca. Manage them in the
   Alpaca paper dashboard. SEMIEDGE has no command that changes them.
