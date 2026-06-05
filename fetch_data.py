#!/usr/bin/env python3
"""
fetch_data.py — pull training data from the intervals.icu API and write data.json
for the marathon telemetry dashboard. Runs daily in GitHub Actions.
"""

import os, sys, json, datetime as dt
from collections import defaultdict

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

BASE = "https://intervals.icu/api/v1"
ATHLETE = os.environ.get("INTERVALS_ATHLETE_ID", "").strip()
KEY = os.environ.get("INTERVALS_API_KEY", "").strip()
UNIT = (os.environ.get("UNIT") or "km").strip().lower()
WEEKS = int(os.environ.get("PLAN_WEEKS") or "18")
HR_TARGET = int(os.environ.get("HR_TARGET") or "150")
DEF_START = os.environ.get("PLAN_START") or "2026-06-08"
DEF_RACE = os.environ.get("RACE_DATE") or "2026-10-11"

M_PER_UNIT = 1609.344 if UNIT == "mi" else 1000.0
RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}

ZONE_COLORS = ["#2f7d52", "#34e07d", "#ffb43a", "#ff8a4a", "#ff5e6c"]
ZONE_NAMES = ["Z1 recovery", "Z2 aerobic", "Z3 tempo", "Z4 threshold", "Z5 VO2"]

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "data.json")) as f:
    OUT = json.load(f)


def monday(d):
    return d - dt.timedelta(days=d.weekday())


def plan_start():
    return monday(dt.date.fromisoformat(DEF_START))


def api(path, params=None):
    r = requests.get(
        BASE + path,
        params=params or {},
        auth=("API_KEY", KEY),
        timeout=30
    )

    if not r.ok:
        print("URL:", r.url)
        print("STATUS:", r.status_code)
        print("BODY:", r.text)

    r.raise_for_status()
    return r.json()


def hms(seconds):
    s = int(round(seconds))
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"


def first(d, *keys):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def riegel_predict(activities):
    best = None
    cutoff = dt.date.today() - dt.timedelta(weeks=6)

    for a in activities:
        if a.get("type") not in RUN_TYPES:
            continue

        dist = a.get("distance") or 0
        t = a.get("moving_time") or a.get("elapsed_time") or 0
        d = (a.get("start_date_local") or "")[:10]

        if dist < 5000 or t <= 0 or not d:
            continue

        if dt.date.fromisoformat(d) < cutoff:
            continue

        proj = t * (42195.0 / dist) ** 1.06
        best = proj if best is None else min(best, proj)

    return best


def main():
    if not ATHLETE or not KEY:
        print("No credentials set — exiting.")
        return

    start = plan_start()
    today = dt.date.today()
    race = dt.date.fromisoformat(DEF_RACE)

    # -----------------------------
    # SAFE DATE WINDOW (FIXED)
    # -----------------------------
    if today < start:
        print("Plan has not started yet — skipping API sync.")
        return

    api_oldest = start.isoformat()
    api_newest = today.isoformat()

    print("START =", start)
    print("TODAY =", today)
    print("API_OLDEST =", api_oldest)
    print("API_NEWEST =", api_newest)

    acts = api(
        f"/athlete/{ATHLETE}/activities",
        {
            "oldest": api_oldest,
            "newest": api_newest,
            "fields": "name,type,start_date_local,distance,moving_time,elapsed_time,"
                      "icu_training_load,average_heartrate,decoupling,icu_hr_zone_times"
        }
    )

    well = api(
        f"/athlete/{ATHLETE}/wellness",
        {"oldest": api_oldest, "newest": api_newest}
    )

    # -----------------------------
    # WEEK CALC
    # -----------------------------
    weeks_done = min(WEEKS, max(1, ((monday(today) - start).days // 7) + 1))

    vol = [0.0] * WEEKS
    longrun = [0.0] * WEEKS
    tss = [0.0] * WEEKS
    long_decouple = [None] * WEEKS
    runs_total = 0
    day_kind = defaultdict(lambda: "rest")

    for a in acts:
        if a.get("type") not in RUN_TYPES:
            continue

        d = (a.get("start_date_local") or "")[:10]
        if not d:
            continue

        wk = (monday(dt.date.fromisoformat(d)) - start).days // 7
        if not (0 <= wk < WEEKS):
            continue

        km = (a.get("distance") or 0) / M_PER_UNIT
        vol[wk] += km
        tss[wk] += a.get("icu_training_load") or 0
        runs_total += 1
        day_kind[d] = "easy"

        if km > longrun[wk]:
            longrun[wk] = km
            long_decouple[wk] = a.get("decoupling")
            day_kind[d] = "long"

    # -----------------------------
    # WELLNESS
    # -----------------------------
    well_by_date = {w.get("id"): w for w in well if w.get("id")}

    ctl = [None] * WEEKS
    atl = [None] * WEEKS
    rhr = [None] * WEEKS
    hrv = [None] * WEEKS
    weight = [None] * WEEKS
    vo2 = [None] * WEEKS

    for i in range(WEEKS):
        wk_end = start + dt.timedelta(weeks=i, days=6)

        for back in range(7):
            w = well_by_date.get((wk_end - dt.timedelta(days=back)).isoformat())
            if not w:
                continue

            if w.get("ctl") is not None:
                ctl[i] = round(w["ctl"], 1)
            if w.get("atl") is not None:
                atl[i] = round(w["atl"], 1)

            rhr[i] = w.get("restingHR") or rhr[i]
            hrv[i] = w.get("hrv") or hrv[i]

            if w.get("weight"):
                weight[i] = round(w["weight"], 1)

            v = first(w, "vo2max", "VO2max")
            if v:
                vo2[i] = round(v, 1)

            break

    trim = lambda arr: arr[:weeks_done]
    keep = lambda arr: [x for x in trim(arr) if x is not None]

    # -----------------------------
    # META
    # -----------------------------
    m = OUT["meta"]
    m["unit"] = UNIT
    m["week"] = weeks_done
    m["totalWeeks"] = WEEKS
    m["daysToRace"] = max(0, (race - today).days)
    m["updated"] = today.isoformat()
    m["live"] = True

    OUT["weeks"] = [f"W{i+1}" for i in range(WEEKS)]

    OUT["volume"]["done"] = [round(vol[i], 1) if i < weeks_done else None for i in range(WEEKS)]
    OUT["volume"]["longrun"] = [round(longrun[i], 1) if i < weeks_done else None for i in range(WEEKS)]

    OUT["tss"] = [round(tss[i]) for i in range(weeks_done)]

    if keep(ctl) and keep(atl):
        OUT["pmc"]["ctl"] = keep(ctl)
        OUT["pmc"]["atl"] = keep(atl)

    if keep(rhr):
        OUT["rhr"] = keep(rhr)

    if keep(hrv):
        OUT["hrv"] = keep(hrv)

    if keep(weight):
        OUT["weight"] = keep(weight)

    if keep(vo2):
        OUT["vo2"] = keep(vo2)
        m["vo2"] = keep(vo2)[-1]

    OUT["aero"]["decoupling"] = [
        round(x, 1) if x is not None else None for x in long_decouple[:weeks_done]
    ]

    # -----------------------------
    # KPIs
    # -----------------------------
    OUT["kpi"]["totalVol"] = str(round(sum(vol[:weeks_done])))
    OUT["kpi"]["volAvg"] = f"{weeks_done} wks · {round(sum(vol[:weeks_done])/weeks_done)}/wk avg"

    ck = keep(ctl)
    if len(ck) >= 2:
        ramp = round(ck[-1] - ck[-2], 1)
        OUT["kpi"]["ramp"] = f"+{ramp}"

    # -----------------------------
    # SAVE
    # -----------------------------
    with open(os.path.join(HERE, "data.json"), "w") as f:
        json.dump(OUT, f, indent=2)

    print(f"Done — weeks {weeks_done}/{WEEKS}, runs {runs_total}")


if __name__ == "__main__":
    main()
