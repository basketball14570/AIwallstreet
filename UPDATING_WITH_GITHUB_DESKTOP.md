# One-time setup so you never re-download a ZIP again

The ZIP dance is painful because every download is a brand-new folder with no
`.env`. **GitHub Desktop** fixes that: you clone the project once, your `.env`
stays put, and every future update is two clicks + a rebuild.

## One-time setup (~5 minutes)

1. **Install GitHub Desktop** (free): https://desktop.github.com/ → install →
   sign in with your GitHub account (the `basketball14570` one).

2. **Clone the project:**
   - In GitHub Desktop: **File → Clone repository…**
   - Pick **`basketball14570/aiwallstreet`** from the list.
   - Local path: choose something simple like `C:\AIwallstreet-git`.
   - Click **Clone**.

3. **Switch to the working branch:**
   - At the top, click the **Current branch** dropdown (it'll say `main`).
   - Choose **`claude/brave-hopper-TZQTh`**.
   - This is the branch with all the latest work; you only do this once.

4. **Create your `.env` (once, and it stays forever):**
   - Open the cloned folder (`C:\AIwallstreet-git`) in File Explorer.
   - Copy `.env.example` → rename the copy to **`.env`**.
   - Open `.env` in Notepad, fill in:
     - `POLYGON_API_KEY=` your (rotated) key
     - `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=`
   - Save. (`.env` is ignored by Git, so pulling updates never touches it.)

5. **Start it:**
   - In the folder's File Explorer address bar, type `powershell`, Enter.
   - Run: `docker compose up --build`
   - Open http://localhost:8000

## Getting an update later (the whole point)

Whenever I push a change, you just:

1. **GitHub Desktop** → click **Fetch origin**, then **Pull origin** (same
   button, it changes label once there's something to pull).
2. Back in PowerShell: press **Ctrl + C** to stop, then run
   `docker compose up --build` again.

That's it. No new folder, no re-extracting, no recreating `.env`. The database
migrations run automatically on startup, so you do **not** need `down -v` for
normal updates — only if you're clearing out bad data after a crash.

## Quick "am I current?" check

After pulling + rebuilding, open http://localhost:8000/docs and confirm you see
`/alerts/test` and `/analysis/{ticker}`. If they're there, you're on the latest.
