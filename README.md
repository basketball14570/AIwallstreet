# AIwallstreet — Unusual Options Flow Intelligence Platform

Real-time engine that ingests unusual options activity, scores it with a
transparent weighted model (with an ML upgrade path), and emits **probabilistic
setups** — not buy/sell signals. The goal is to surface flow patterns that
historically precede violent upside moves while filtering routine hedging and
low-quality flow.

> Educational / research tooling. Not investment advice. Validate every signal
> against your own risk process before trading.

---

## 1. System architecture

```
                       ┌────────────────────────────────────────────────┐
   DATA SOURCES        │                 INGEST LAYER                     │
 ┌───────────────┐     │  FlowProvider.stream()  (Unusual Whales, ...)    │
 │ Unusual Whales│────►│  ContextProvider.get_context()  (Polygon, float, │
 │ Polygon.io    │     │     short interest, borrow, gamma, sentiment)    │
 │ Dark pool     │     └───────────────┬──────────────────────────────────┘
 │ Social/news   │                     │ normalised FlowEvent + MarketContext
 └───────────────┘                     ▼
                       ┌────────────────────────────────────────────────┐
                       │            REAL-TIME PIPELINE (async)            │
                       │  SweepTracker → features → ScoringEngine →       │
                       │  Classifier → persist(Postgres) → publish(Redis) │
                       └───────┬───────────────────────┬──────────────────┘
                               │                       │
              ┌────────────────▼──────┐        ┌───────▼─────────────────┐
              │  ALERT DISPATCHER     │        │  REDIS pub/sub           │
              │  Discord / Telegram   │        │  flow.events             │
              │  (only if confidence  │        └───────┬─────────────────┘
              │   ≥ threshold)        │                │
              └───────────────────────┘        ┌───────▼─────────────────┐
                                                │  FastAPI + WebSocket     │
   ┌─────────────────────────┐                 │  /flow /watchlist /ws    │
   │ ML PIPELINE (offline)    │                 │  → Web dashboard         │
   │ labeling → train →       │                 └──────────────────────────┘
   │ calibrate → models/*.pkl │
   └──────────┬───────────────┘
              │ loaded by ScoringEngine (optional override)
   ┌──────────▼───────────────┐
   │ BACKTEST / REPLAY        │  same scoring code, joined to outcomes
   └──────────────────────────┘
```

**Key principle:** the live pipeline, the backtester, and the ML feature space
all call the *same* `app.scoring.classifier.classify()`. There is one
definition of a "setup", so backtest results are faithful to production.

---

## 2. Folder structure

```
app/
  config.py              # env-driven settings
  main.py                # FastAPI app (API)
  worker.py              # standalone real-time pipeline process
  core/                  # logging, redis pub/sub
  db/                    # async engine + SQLAlchemy schema
  schemas/               # Pydantic domain types (FlowEvent, ScoreResult, ...)
  providers/             # data ingestion (UW flow, Polygon context, sentiment)
  scoring/               # features → engine (weighted) → classifier   ← core IP
  pipeline/realtime.py   # ingest→score→persist→alert orchestration
  alerts/                # Discord, Telegram, summary generation
  api/routes/            # flow, watchlist, websocket endpoints
  ml/                    # feature engineering, labeling, training+calibration
  backtest/engine.py     # replay + metrics (hit rate, precision/recall, ...)
tests/                   # scoring engine sanity tests
```

---

## 3. Database schema

| Table | Purpose |
|-------|---------|
| `raw_flow` | normalised options-flow events as ingested |
| `flow_features` | engineered feature vector per event (1:1) |
| `flow_score` | classification + 4 probabilities + component scores + reasons |
| `alert` | dispatched alerts (audit trail, dedup, delivery status) |
| `watchlist` | user-curated tickers |
| `outcome` | realised forward returns for labelling + backtest |

See `app/db/models.py`. MVP uses `create_all`; use Alembic for production
migrations.

---

## 4. API design

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/health` | liveness |
| POST | `/flow/score` | score an arbitrary `FlowEvent` (+ optional context); no persistence |
| GET  | `/flow/recent` | recent scored flow, filter by `min_confidence` / `classification` |
| GET/POST/DELETE | `/watchlist` | watchlist CRUD |
| WS   | `/ws/flow` | live stream of scored flow (bridges Redis `flow.events`) |

Interactive docs at `/docs` once running.

---

## 5. Scoring logic

`app/scoring/engine.py` computes six **component scores** in `[0,1]`:

- **conviction** — ask-side ratio, sweep urgency, repeated sweeps, midpoint penalty
- **volume_confirmation** — relative options volume, stock RVOL, OI change
- **squeeze_fuel** — low float, short interest %, borrow rate, negative dealer gamma
- **catalyst** — social + news sentiment alignment
- **geometry** — near-dated, slightly-OTM speculative footprint
- **historical** — similarity to known squeeze setups

These combine (weights in `ScoringWeights`, tunable/learnable) into four
**probabilities**:

- `fake_flow_prob` — midpoint fills, bid-side, no volume backing
- `momentum_prob` — conviction × volume confirmation
- `squeeze_prob` — squeeze fuel × conviction
- `explosion_prob` — soft-AND of momentum & squeeze, damped by fake-flow

`app/scoring/classifier.py` maps these to a `confidence` (0–100) and a label:
`Normal Flow → Hedging → Watchlist Candidate → Strong Momentum Setup →
Potential Explosion Setup` (plus `Fake / Low-Quality Flow`). Every score carries
human-readable `reasons` — that is the "explain WHY" output used in alerts.

---

## 6. ML pipeline

- **Labeling** (`ml/labeling.py`): triple-barrier on forward returns — positive
  if the underlying ran up past `+20%` before breaching `-10%` within horizon.
  No look-ahead bias.
- **Features** (`ml/feature_engineering.py`): the rules-engine inputs *are* the
  ML features, so the model is a learned re-weighting of the same signals.
- **Training** (`ml/train.py`): time-ordered split → `GradientBoosting` wrapped
  in `CalibratedClassifierCV` (isotonic) for calibrated probabilities → reports
  precision / recall / PR-AUC → persisted with joblib.
- **False-positive reduction**: optimise precision at a fixed daily alert budget
  (top-k), class-weighting for rarity, and a two-stage fake-flow filter before
  the explosion ranker.
- **Model progression**: GradientBoosting (MVP) → LightGBM/XGBoost with
  monotonic constraints on squeeze fuel → two-stage filter+ranker.

Run the demo (synthetic data, no DB needed): `python -m app.ml.train`.

---

## 7. Real-time processing flow

`app/pipeline/realtime.py`:
`stream → enrich context (cached in Redis) → SweepTracker (repeated-sweep
detection over a 10-min window) → build features → classify → persist
(raw_flow + flow_features + flow_score) → publish to Redis → dispatch alerts if
confidence ≥ threshold → record alert`.

---

## 8. Backtesting

`app/backtest/engine.py` replays events through the production scoring path and
reports: **hit rate, average move after alert, average max run-up, worst
drawdown, precision, recall, and setup quality per week**. Because it reuses
`classify()`, backtest and live behaviour cannot drift.

---

## 9. Quickstart

```bash
# Option A: full stack
cp .env.example .env            # add keys (works without them — uses mock data)
docker compose up --build       # API :8000, worker, Postgres, Redis

# Option B: local API + pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload   # API
python -m app.worker            # real-time pipeline (separate shell)

# Tests
pytest -q

# Try the engine without any infra:
curl -s -X POST localhost:8000/flow/score -H 'content-type: application/json' -d '{
  "source":"manual","ticker":"GME","contract_type":"call","strike":22,
  "expiry":"2026-06-19T00:00:00Z","side":"ask","is_sweep":true,
  "premium":750000,"size":2000,"spot":20,"observed_at":"2026-05-22T15:00:00Z"}'
```

Without API keys the providers emit realistic **synthetic** flow + context, so
the whole pipeline runs end-to-end offline for development.

---

## 10. MVP scope (this repo) vs. later

**Shipped MVP:** normalised ingest with mock fallback, weighted+explainable
scoring engine, classification + 4 probabilities, async pipeline, Postgres
schema, Redis pub/sub, WebSocket stream, Discord/Telegram alerts, watchlist API,
triple-barrier labeling, calibrated training script, backtest engine, tests.

**Deliberately stubbed (clear extension points):** real UW/Polygon field
mapping, live sentiment clients (tweepy/PRAW/NewsAPI), dark-pool feed, a
front-end dashboard, Alembic migrations, auth.

---

## 11. Scaling recommendations

- **Ingestion**: shard `FlowProvider`s by ticker universe across worker
  processes; switch UW polling to its websocket where available.
- **Throughput**: replace in-process orchestration with a stream bus (Redis
  Streams → Kafka/Redpanda) and stateless scoring consumers; key by ticker for
  ordered repeated-sweep tracking.
- **State**: move `SweepTracker` and context cache fully into Redis so workers
  are stateless and autoscale.
- **Storage**: partition `raw_flow`/`flow_score` by day; TimescaleDB or
  ClickHouse for time-series analytics and fast backtests.
- **Serving**: separate API, WebSocket fan-out, and pipeline workers; put the
  WS layer behind a pub/sub fan-out service.
- **ML**: nightly retrain + calibration job, champion/challenger model registry,
  monitor live precision vs. backtest for drift.
```
