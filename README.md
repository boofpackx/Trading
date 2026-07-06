# ICT NY-AM Bot

A one-click-confirm trading bot for NQ built around ICT concepts, tuned for the
New York AM session and Topstep funded-account rules, with a minimalist
grayscale "e-ink" web UI.

**The strategy:** sweep of an internal low/high → displacement → OTE (61.8–79%)
fib retracement entry → SMT divergence (NQ vs ES) filter → target the nearest
internal high/low.

## The rules it encodes

| Rule | Value |
|---|---|
| Session | entries 9:30–11:00 ET only; unfilled orders cancelled at 11:00 |
| Flat before close | everything force-closed by 15:55 ET |
| Structure / entry | 5-minute structure, 1-minute entries |
| Risk per trade | $250 fixed — sized in NQ minis, falls back to MNQ micros |
| Minimum R:R | 1:1 against the take-profit |
| Take profit | 30–50 NQ points from entry (internal high/low used when inside the band, else clamped); classic internal-liquidity targeting via `target_mode="internal"` |
| Entry | limit at the 70.5% OTE sweet spot of the impulse leg |
| Stop | 4 ticks beyond the sweep extreme |
| SMT filter | NQ vs ES divergence required at the raid |
| Daily guardrails | halt after 2 losses or −$500 (half the Topstep 50K daily loss limit) |
| Contract caps | 5 minis / 50 micros (Topstep 50K scaling) |
| Execution | bot stages the trade; **you** click Confirm |

## Analytics & journal

Every closed trade is journaled to `journal.<mode>.jsonl` (sim and live kept
separate) and the **Analytics** tab computes from the full history:

- Total P&L and **return % on the 50K account**, plus **monthly return %** table
- **Win rate, profit factor, expectancy per trade, average R-multiple**
- Average win / average loss, best/worst, current streak, **max drawdown**
- **Equity curve** with per-trade hover, and a trade-history table with
  R-multiples and exit reasons
- CSV export at `/api/journal.csv`

In live mode the journal also restores today's stats after a restart, so the
daily-loss guardrails can't be reset by bouncing the bot mid-session.

## Quality of life

- **Sim speed controls** (pause / 1x / 4x / 10x) in the header
- **Keyboard shortcuts**: `C` confirm, `S` skip, `F` flatten
- Browser-tab alert (`● SETUP`) when a trade is staged

## Live demo on GitHub Pages

`docs/` holds a fully static build of the dashboard that replays a recorded
sim session (setup → confirm → fill → exit) on a loop — no server needed.
To publish it: **Settings → Pages → Deploy from a branch → `main` + `/docs`**,
then it appears at `https://<user>.github.io/Trading/`. Regenerate the
recording any time with `python tools/build_demo.py [seed]`.

## Quick start (simulation)

```bash
pip install -r requirements.txt
python -m bot
# open http://127.0.0.1:8000
```

Sim mode replays a synthetic correlated NQ/ES session (1 sim-minute ≈ 0.8 s)
starting pre-market, so structure exists at the 9:30 open. Watch the log for
`impulse leg → SETUP READY`, then Confirm or Skip.

## Live trading (TopstepX / ProjectX)

```bash
cp .env.example .env
# fill in PROJECTX_USERNAME + PROJECTX_API_KEY (TopstepX → Settings → API)
BOT_MODE=live python -m bot
```

Live mode authenticates against the ProjectX Gateway API, polls 1m bars for NQ
and ES, and on Confirm places a limit entry with attached stop-loss and
take-profit brackets on your account. If login fails it falls back to sim.

## Layout

```
bot/
  config.py            # every strategy/session/risk parameter in one place
  models.py            # Candle, Swing, Setup, Position, TradeResult
  risk.py              # $250 sizing (NQ→MNQ fallback) + daily guardrails
  execution.py         # staging, one-click confirm, fills, flat-by rule
  server.py            # FastAPI + websocket, runs the loop, serves the UI
  strategy/
    structure.py       # fractal swings, sweeps, internal liquidity
    fib.py             # OTE zone / 70.5% entry math
    smt.py             # NQ-vs-ES divergence
    engine.py          # sweep → displacement → OTE → SMT state machine
  data/
    sim.py             # correlated synthetic feed (demo/paper)
    projectx.py        # TopstepX gateway client (auth, bars, brackets)
    aggregator.py      # 1m → 5m
ui/index.html          # grayscale e-ink dashboard (no dependencies)
tests/                 # strategy math + engine end-to-end
```

## Tests

```bash
python -m pytest tests/ -q
```

## Notes & limits

- **Not financial advice; futures are risky.** The sim proves the plumbing,
  not the edge — forward-test on a practice account before funding it.
- Live data is polled every 5 s from `retrieveBars`; a SignalR streaming
  upgrade is a natural next step.
- Position exits in live mode rely on the exchange-side brackets; the local
  tracker mirrors them for the UI and enforces the 15:55 flatten.
