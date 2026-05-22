# Running AIwallstreet on your own Windows PC (free)

This walks you through running the whole app on your computer, plugged into your
**Polygon** options-flow key, so you can confirm it works on real data **before
paying for any hosting**. Everything here is free.

You need: **Docker Desktop** (you said it's installed) and your **Polygon API
key**. That's it.

---

## Step 1 — Make sure Docker Desktop is running

1. Open **Docker Desktop** from the Start menu.
2. Wait until the whale icon in the bottom-left is green / says "Engine
   running." If it asks to update or enable WSL, say yes.

If Docker Desktop isn't installed, get it free from
https://www.docker.com/products/docker-desktop/ and restart your PC after
installing.

---

## Step 2 — Get the code onto your PC

The latest version (with the dashboard signals) is on the branch
`claude/brave-hopper-TZQTh`.

1. Go to the repository on github.com.
2. Click the branch dropdown (it usually says `main`) and pick
   **`claude/brave-hopper-TZQTh`**.
3. Click the green **Code** button → **Download ZIP**.
4. Right-click the downloaded ZIP → **Extract All** → pick a folder you'll
   remember, e.g. `C:\AIwallstreet`.

---

## Step 3 — Create your settings file (with your Polygon key)

The app reads its settings from a file named **`.env`** in the project folder.
It is NOT included in the download (keys are kept private), so you make it once.

1. In the project folder (`C:\AIwallstreet`), find the file **`.env.example`**.
2. Copy it and rename the copy to exactly **`.env`** (no `.example`, no `.txt`).
   - Tip: if Windows hides file extensions, turn on "File name extensions" in
     File Explorer's **View** menu so you don't accidentally create `.env.txt`.
3. Open `.env` in **Notepad** and find this line:
   ```
   POLYGON_API_KEY=
   ```
   Paste your Polygon key right after the `=` (no spaces, no quotes):
   ```
   POLYGON_API_KEY=your_polygon_key_here
   ```
4. A couple lines down, make sure it says:
   ```
   FLOW_PROVIDER=polygon
   ```
5. Save and close Notepad.

Everything else can stay at its defaults.

---

## Step 4 — Start the app

1. In File Explorer, open your project folder (`C:\AIwallstreet`).
2. Click in the address bar at the top, type **`powershell`**, and press Enter.
   A blue PowerShell window opens already pointing at the folder.
3. Type this and press Enter:
   ```
   docker compose up --build
   ```
4. The first run takes a few minutes (it's downloading and building). You'll see
   a lot of text scroll by — that's normal. It's ready when the scrolling slows
   and you see lines mentioning `Uvicorn running` / `polygon flow starting`.

---

## Step 5 — Open the dashboard

In your web browser, go to:

```
http://localhost:8000
```

You'll see the live flow dashboard. **Leave the PowerShell window open** — closing
it stops the app.

---

## Step 6 — The one thing that tells you if your Polygon plan works

This is the whole point of the exercise. **During US market hours
(9:30 AM – 4:00 PM Eastern, weekdays)**, look in the PowerShell window for a line
that says:

```
polygon snapshot field coverage ... last_quote_pct=.. last_trade_pct=.. implied_volatility_pct=.. open_interest_pct=..
```

Read the four numbers:

| Field | What it means | Want |
|---|---|---|
| `open_interest_pct` | needed to detect "unusual" volume | near **100** |
| `implied_volatility_pct` | needed for the IV-rank signal | near **100** |
| `last_quote_pct` / `last_trade_pct` | needed to tell buys from sells (conviction) | near **100** |

- **All near 100** → your Polygon plan has everything the app needs. It works on
  real data. 🎉
- **`last_quote`/`last_trade` are 0 but you're checking *outside* market hours** →
  that's expected. Re-check during market hours.
- **They're 0 *during* market hours** → your Polygon tier doesn't include options
  quotes/trades. That's the thing to know before paying for hosting — you'd need
  to upgrade the Polygon plan (or the app runs with weaker direction signals).

If you also see warnings in the log, they spell out exactly which entitlement is
missing — they're written to be read by a human.

---

## Stopping it

- In the PowerShell window, press **Ctrl + C**.
- Then type `docker compose down` and press Enter to clean up.

To start it again later, just repeat Step 4 (it'll be fast after the first time).

---

## If something goes wrong

- **"docker: command not found" / nothing happens** → Docker Desktop isn't
  running. Go back to Step 1.
- **Dashboard won't open at localhost:8000** → give it another minute; the first
  build is slow. Check the PowerShell window for errors.
- **Dashboard loads but no flow appears** → outside market hours there may be
  little/no new volume; the synthetic fallback only kicks in if the key is
  missing or not authorized. Check the coverage log (Step 6).
- **Lots of red text on first start** → scroll up to the *first* error; often
  it's a typo in `.env` (e.g. `.env.txt` instead of `.env`).

You never have to type anything except the two commands in Steps 4 and the stop
section. If you get stuck, copy the last ~20 lines from PowerShell and send them
to me.
