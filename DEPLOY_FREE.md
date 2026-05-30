# Host AIwallstreet online for free

You don't want phone push (Telegram/Discord) — you'll just open the **website**
and watch the live feed, the breakout screener, and the stock-analysis tab. This
guide gets that website online **for free** and (optionally) on a subdomain of
**onequoteins.com**.

## What makes this free

The app normally wants Postgres + Redis + several processes. For free hosting it
runs as **one container with no external services**:

| Normally | Free "lite" mode |
|---|---|
| Postgres | **SQLite** (a file) — `DATABASE_URL=sqlite+aiosqlite:////tmp/app.db` |
| Redis | **in-memory bus** — `REDIS_URL=` (blank) |
| separate worker | **in-process pipeline** — `RUN_PIPELINE_INPROCESS=true` |

Everything works: the live options-flow feed, the **Breakout screener** tab, and
the **Analyze stock** tab (support/resistance + options playbook). Without a data
key it uses realistic **synthetic** data so it's functional immediately; add a
Polygon key later for live market data.

---

## Option A — Render (recommended, easiest)

1. Make sure this repo is on your GitHub (it is: `basketball14570/aiwallstreet`).
2. Go to **render.com**, sign up (free), and click **New + → Blueprint**.
3. Connect the repo. Render reads **`render.yaml`** in this project and creates a
   free web service with all the lite-mode settings already filled in.
4. Click **Apply / Deploy**. First build takes a few minutes.
5. Open the `https://aiwallstreet-xxxx.onrender.com` URL it gives you — that's
   your dashboard. Bookmark it on your phone.

> Free Render web services **sleep after ~15 min of inactivity** and take ~30–60s
> to wake on the next visit. The SQLite file resets on each redeploy — fine here,
> since the screener/analysis recompute live and flow re-accumulates.

### Put it on app.onequoteins.com (optional)

1. In the Render service → **Settings → Custom Domains → Add** `app.onequoteins.com`.
2. Render shows a **CNAME target** (like `aiwallstreet-xxxx.onrender.com`).
3. At whoever manages **onequoteins.com** DNS (your registrar/host), add a record:
   - Type **CNAME**, Name **app**, Value **the Render target**.
4. Wait for DNS (minutes–an hour). Render auto-issues a free SSL cert. Done —
   your main insurance site at `onequoteins.com` is untouched.

---

## Option B — other free hosts

The same container works anywhere that runs a Dockerfile and injects `$PORT`:

- **Fly.io** — `fly launch` (needs a card on file but has a free allowance). Set
  the same four env vars.
- **Hugging Face Spaces** (Docker SDK) — free, but set the port to `7860`
  (`PORT=7860`) and the env vars above.
- **Koyeb** — free web service from a Dockerfile.

Set these env vars on any of them:

```
DATABASE_URL=sqlite+aiosqlite:////tmp/app.db
REDIS_URL=
RUN_PIPELINE_INPROCESS=true
FLOW_PROVIDER=synthetic
```

---

## Run it on your own PC instead (no hosting)

```bash
pip install -r requirements.txt
DATABASE_URL=sqlite+aiosqlite:///./app.db REDIS_URL= RUN_PIPELINE_INPROCESS=true \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/`. Reachable only on that computer while it's on.

---

## Going live: one important caveat

**There is no login yet — anyone with the URL can see the dashboard.** That's
fine for a private bookmark, but before you link it anywhere public on
`onequoteins.com` you should add at least a password. Ask and I'll wire up simple
auth (an env-set username/password, or a shared access token) — it's a small
change.

## Upgrade to live market data

Add `POLYGON_API_KEY` (and `ANTHROPIC_API_KEY` for the AI take) in your host's
environment settings, and set `FLOW_PROVIDER=polygon`. No code change needed.
