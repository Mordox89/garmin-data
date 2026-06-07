# Marathon Telemetry — daily auto-fed dashboard

A static dashboard that refreshes itself once a day from your **intervals.icu**
data via a GitHub Action. No server, no stored Garmin password, free to run.

```
index.html              the dashboard (reads data.json)
data.json               the data it shows (overwritten daily by the Action)
fetch_data.py           pulls intervals.icu -> writes data.json
requirements.txt
.github/workflows/sync.yml   runs fetch_data.py every morning + commits data.json
```

## How it flows

```
Garmin ──auto-sync──> intervals.icu ──API──> GitHub Action (daily) ──> data.json ──> dashboard
```

intervals.icu does the heavy lifting (it computes CTL / ATL / TSB / TSS and stores
your Garmin resting-HR, HRV and weight). The Action just reads that and reshapes it.

## One-time setup (~15 min)

You do these steps yourself — I never see your key, and it only ever lives in a
GitHub Secret, never in the browser.

1. **Connect Garmin (or Strava) to intervals.icu.** Create a free account at
   intervals.icu and link your device under Settings. Let it backfill your history.

2. **Get your API key + athlete id.** intervals.icu → **Settings → Developer**.
   Copy the *API key* and your *Athlete ID* (looks like `i123456`).

3. **Create a GitHub repo** and upload these files (keep the folder structure,
   including `.github/workflows/sync.yml`).

4. **Add the secrets.** Repo → **Settings → Secrets and variables → Actions → New
   repository secret**:
   - `INTERVALS_ATHLETE_ID` = your athlete id
   - `INTERVALS_API_KEY` = your API key

   *(Optional, as “Variables” on the same page:)* `PLAN_START` (first Monday, e.g.
   `2026-06-01`), `PLAN_WEEKS` (`18`), `RACE_DATE` (`2026-10-11`), `UNIT` (`mi` or
   `km`), `PLAN_MILEAGE` (comma list of weekly planned distance for the plan curve).

5. **Turn on Pages.** Repo → **Settings → Pages → Source: Deploy from branch →
   main / root**. Your dashboard goes live at `https://<you>.github.io/<repo>/`.

6. **Run it once.** Repo → **Actions → “Sync training data” → Run workflow**.
   After ~30 s `data.json` is refreshed and the dashboard flips from the amber
   “sample” badge to a green **live** badge. After that it updates itself daily.

## What's live vs. what's still sample

**Live from intervals.icu** — weekly volume, long-run progression & %, weekly TSS,
CTL/ATL/TSB (PMC), CTL ramp, resting-HR, HRV, weight, VO₂max trend, the 80/20 zone
split, long-run decoupling, pace at your reference HR, the consistency heatmap, the
race-time predictor (Riegel), and the header KPIs.

**Still sample / manual** — the marathon-pace HR analytics panel (auto-detecting
"MP sessions" reliably needs you to tag those workouts in intervals.icu, so it stays
on placeholder rather than guessing wrong), plus the shoe and habit logs, which are
manual by nature. The dashboard stays fully intact — those panels just keep their
placeholder numbers until wired.

Block config (start 2026-06-08, race 2026-10-11, kilometres, 18 weeks) is baked into
`fetch_data.py` as defaults, so you only need the two secrets below. Override any of
them with repo Variables if the plan changes.

## Running locally to test

```bash
pip install -r requirements.txt
INTERVALS_ATHLETE_ID=i123456 INTERVALS_API_KEY=xxxx python fetch_data.py
# then open index.html through a local server (not file://) so it can fetch data.json:
python -m http.server 8000   # visit http://localhost:8000
```

Opening `index.html` directly as a file still works — it just falls back to the
sample data, because browsers block `fetch()` on `file://`.
