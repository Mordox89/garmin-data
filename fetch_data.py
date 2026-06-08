#!/usr/bin/env python3
"""
fetch_data.py — pull training data from the intervals.icu API and write data.json
for the marathon telemetry dashboard. Runs daily in GitHub Actions.

Secrets / config (env vars; sensible defaults baked in for this block):
  INTERVALS_ATHLETE_ID   required  (intervals.icu > Settings > Developer)
  INTERVALS_API_KEY      required
  PLAN_START   default 2026-06-08   (first Monday of the block)
  RACE_DATE    default 2026-10-11
  PLAN_WEEKS   default 18
  UNIT         default km           ("km" or "mi")
  HR_TARGET    default 150          (bpm reference for the pace-at-fixed-HR panel)
  PLAN_MILEAGE optional comma list of planned weekly distance (the grey plan curve)

Auth: HTTP Basic, username "API_KEY", password = your key.
"""

import os, sys, json, re, datetime as dt
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
    OUT = json.load(f)   # baseline so the JSON always stays complete


def monday(d): return d - dt.timedelta(days=d.weekday())


def plan_start():
    return monday(dt.date.fromisoformat(DEF_START))


def api(path, params=None):
    r = requests.get(BASE + path, params=params or {}, auth=("API_KEY", KEY), timeout=30)
    r.raise_for_status()
    return r.json()


def hms(seconds):
    s = int(round(seconds)); return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"


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
        if dist < 5000 or t <= 0 or not d or dt.date.fromisoformat(d) < cutoff:
            continue
        proj = t * (42195.0 / dist) ** 1.06
        best = proj if best is None else min(best, proj)
    return best


def main():
    if not ATHLETE or not KEY:
        print("No credentials set — leaving sample data.json untouched.")
        return

    start = plan_start()
    today = dt.date.today()
    race = dt.date.fromisoformat(DEF_RACE)
    weeks_done = max(1, min(WEEKS, (monday(today) - start).days // 7 + 1))

    # The API requires oldest <= newest. Before the block starts (today < start)
    # there is no data yet, so query a valid (empty) range instead of crashing.
    api_oldest = min(start, today).isoformat()
    api_newest = today.isoformat()

    acts = api(f"/athlete/{ATHLETE}/activities",
               {"oldest": api_oldest, "newest": api_newest,
                "fields": "name,type,start_date_local,distance,moving_time,elapsed_time,"
                          "icu_training_load,average_heartrate,decoupling,icu_hr_zone_times"})
    well = api(f"/athlete/{ATHLETE}/wellness",
               {"oldest": api_oldest, "newest": api_newest})

    # ---------- volume / load / consistency ----------
    vol = [0.0] * WEEKS; longrun = [0.0] * WEEKS; tss = [0.0] * WEEKS
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

    # ---------- zone distribution (80/20), last 28 days ----------
    zone_secs = [0.0] * 5
    cut28 = today - dt.timedelta(days=28)
    for a in acts:
        if a.get("type") not in RUN_TYPES:
            continue
        d = (a.get("start_date_local") or "")[:10]
        if not d or dt.date.fromisoformat(d) < cut28:
            continue
        z = a.get("icu_hr_zone_times")
        if not z:
            continue
        for i, sec in enumerate(z):
            zone_secs[min(i, 4)] += sec or 0

    # ---------- pace @ ~HR_TARGET (activity-level, per week) ----------
    pace_at_hr = [None] * WEEKS
    for wk in range(weeks_done):
        cands = []
        for a in acts:
            if a.get("type") not in RUN_TYPES:
                continue
            d = (a.get("start_date_local") or "")[:10]
            if not d:
                continue
            if (monday(dt.date.fromisoformat(d)) - start).days // 7 != wk:
                continue
            hr = a.get("average_heartrate"); dist = a.get("distance") or 0
            t = a.get("moving_time") or 0
            if hr and dist > 2000 and t > 0 and abs(hr - HR_TARGET) <= 7:
                cands.append((abs(hr - HR_TARGET), t / (dist / 1000.0)))  # sec/km
        if cands:
            cands.sort()
            pace_at_hr[wk] = round(cands[0][1])

    # ---------- wellness: CTL/ATL/RHR/HRV/weight/VO2max ----------
    well_by_date = {w.get("id"): w for w in well if w.get("id")}
    ctl = atl = None
    ctl = [None]*WEEKS; atl=[None]*WEEKS; rhr=[None]*WEEKS; hrv=[None]*WEEKS
    weight=[None]*WEEKS; vo2=[None]*WEEKS
    for i in range(WEEKS):
        wk_end = start + dt.timedelta(weeks=i, days=6)
        for back in range(7):
            w = well_by_date.get((wk_end - dt.timedelta(days=back)).isoformat())
            if not w:
                continue
            if w.get("ctl") is not None: ctl[i] = round(w["ctl"], 1)
            if w.get("atl") is not None: atl[i] = round(w["atl"], 1)
            rhr[i] = w.get("restingHR") or rhr[i]
            hrv[i] = w.get("hrv") or hrv[i]
            if w.get("weight"): weight[i] = round(w["weight"], 1)
            v = first(w, "vo2max", "VO2max")
            if v: vo2[i] = round(v, 1)
            break

    trim = lambda arr: arr[:weeks_done]
    keep = lambda arr: [x for x in trim(arr) if x is not None]

    # ---------- assemble ----------
    m = OUT["meta"]
    m["unit"] = UNIT; m["week"] = weeks_done; m["totalWeeks"] = WEEKS
    m["daysToRace"] = max(0, (race - today).days); m["updated"] = today.isoformat(); m["live"] = True
    OUT["weeks"] = [f"W{i+1}" for i in range(WEEKS)]

    plan_env = os.environ.get("PLAN_MILEAGE", "").strip()
    if plan_env:
        plan = [float(x) for x in plan_env.split(",")]
        OUT["volume"]["plan"] = (plan + [None] * WEEKS)[:WEEKS]
    OUT["volume"]["done"] = [round(vol[i], 1) if i < weeks_done else None for i in range(WEEKS)]
    OUT["volume"]["longrun"] = [round(longrun[i], 1) if (i < weeks_done and longrun[i]) else None for i in range(WEEKS)]
    OUT["longRunPct"] = [round(longrun[i] / vol[i] * 100) if vol[i] else 0 for i in range(weeks_done)]
    OUT["tss"] = [round(tss[i]) for i in range(weeks_done)]

    if keep(ctl) and keep(atl):
        OUT["pmc"]["ctl"] = keep(ctl); OUT["pmc"]["atl"] = keep(atl)[:len(keep(ctl))]
    if keep(rhr): OUT["rhr"] = keep(rhr)
    if keep(hrv): OUT["hrv"] = keep(hrv)
    if keep(weight): OUT["weight"] = keep(weight)
    if keep(vo2):
        OUT["vo2"] = keep(vo2); m["vo2"] = keep(vo2)[-1]

    # zones / 80-20
    if sum(zone_secs) > 0:
        tot = sum(zone_secs)
        pct = [round(s / tot * 100) for s in zone_secs]
        OUT["zones"] = [{"n": ZONE_NAMES[i], "v": pct[i], "c": ZONE_COLORS[i]} for i in range(5)]
        easy = pct[0] + pct[1]
        OUT["kpi"]["easyHard"] = f"{easy}/{100 - easy}"

    # decoupling (live) + pace@HR (best-effort)
    OUT["aero"]["decoupling"] = [round(long_decouple[i], 1) if long_decouple[i] is not None else None for i in range(weeks_done)]
    if any(p is not None for p in trim(pace_at_hr)):
        OUT["aero"]["paceAtHR"] = [pace_at_hr[i] for i in range(weeks_done)]

    # consistency heatmap from real run days
    cons = []
    for i in range(weeks_done):
        cons.append([day_kind.get((start + dt.timedelta(weeks=i, days=dow)).isoformat(), "rest") for dow in range(7)])
    if cons:
        OUT["consistency"] = cons
        OUT["runcount"] = f"{runs_total} runs · {sum(r.count('rest') for r in cons)} rest days"

    # KPIs
    OUT["kpi"]["totalVol"] = str(round(sum(vol[:weeks_done])))
    OUT["kpi"]["volAvg"] = f"{weeks_done} wks · {round(sum(vol[:weeks_done])/weeks_done)}/wk avg"
    ck = keep(ctl)
    if len(ck) >= 2:
        ramp = round(ck[-1] - ck[-2], 1)
        OUT["kpi"]["ramp"] = f"+{ramp}"
        OUT["kpi"]["rampSt"] = "coral" if ramp > 7 else "amber" if ramp > 5 else "go"
        OUT["kpi"]["rampNote"] = "back off" if ramp > 7 else "upper safe (<6–7)" if ramp > 5 else "safe (<6–7)"

    # predictor
    pred = riegel_predict(acts)
    if pred:
        m["predicted"] = hms(pred)
        per_km = pred / 42.195
        m["predictedPace"] = f"{int(per_km//60)}:{int(per_km%60):02d}/km"
        m["predictedRange"] = f"{hms(pred*0.985)}–{hms(pred*1.015)}"
        if ck:
            base = ck[0] or 1
            OUT["predictorSeconds"] = [round(pred * (ck[-1] / (c or base))) for c in ck]

    # ---------- predictor-input panel (live; replaces the old demo block) ----------
    inputs = []
    vk = keep(vo2)
    if vk:
        if len(vk) >= 2 and (vk[-1] - vk[0]):
            diff = round(vk[-1] - vk[0], 1)
            trend = f"↑ +{diff}" if diff > 0 else f"↓ {abs(diff)}"
        else:
            trend = "intervals.icu"
        inputs.append({"l": "VO₂max", "v": str(int(round(vk[-1]))), "s": trend})
    longs = [d for d in long_decouple[:weeks_done] if d is not None]
    if longs:
        best = min(longs)
        inputs.append({"l": "Best long-run effic.", "v": f"{best:.1f}% drift",
                       "s": "aerobic" if best < 5 else "watch drift"})
    else:
        inputs.append({"l": "Best long-run effic.", "v": "—", "s": "no long run with HR yet"})
    inputs.append({"l": "Last tune-up", "v": "—", "s": "none yet · feeds model"})
    OUT["predictorInputs"] = inputs   # NB: threshold pace removed (not available via intervals.icu)

    # ---------- next 7 days from intervals.icu planned events ----------
    try:
        ev = api(f"/athlete/{ATHLETE}/events",
                 {"oldest": min(start, today).isoformat(),
                  "newest": (today + dt.timedelta(days=6)).isoformat()})
    except Exception as e:
        print("events fetch failed:", e); ev = []

    def classify(name, dist_km, is_race):
        if is_race:
            return "qual"
        n = (name or "").lower()
        if any(w in n for w in ("interval", "threshold", "vo2", "tempo", "fartlek", "rep", "track", "×", "x1", "x ")):
            return "qual"
        if "long" in n or (dist_km and dist_km >= 24):
            return "long"
        if any(w in n for w in ("rest", "day off", "recovery day")):
            return "rest"
        return "easy"

    ev_by_day = {}
    for e in ev:
        cat = (e.get("category") or "").upper()
        if cat and not (cat == "WORKOUT" or cat.startswith("RACE")):
            continue
        d = (e.get("start_date_local") or "")[:10]
        if not d:
            continue
        dist_m = e.get("distance") or e.get("icu_distance") or e.get("distance_target") or 0
        dist_km = round(dist_m / M_PER_UNIT) if dist_m else None
        name = e.get("name") or e.get("description") or "Workout"
        ev_by_day.setdefault(d, []).append((name, dist_km, cat.startswith("RACE")))

    DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    horizon = [today + dt.timedelta(days=o) for o in range(7)]
    if any(day.isoformat() in ev_by_day for day in horizon):
        wk = []
        for off, day in enumerate(horizon):
            label = "Today" if off == 0 else DOW[day.weekday()]
            items = ev_by_day.get(day.isoformat())
            if items:
                name, dist_km, is_race = items[0]
                wk.append({"d": label, "t": classify(name, dist_km, is_race),
                           "ds": name[:42], "km": f"{int(dist_km)} km" if dist_km else "—"})
            else:
                wk.append({"d": label, "t": "rest", "ds": "Rest / unplanned", "km": "—"})
        OUT["week7"] = wk
    else:
        OUT["week7"] = [{"d": "—", "t": "rest",
                         "ds": "No plan in intervals.icu yet", "km": "sync TrainingPeaks"}]

    # ---------- adherence: planned workouts vs completed runs (live) ----------
    run_dates = {d for d, v in day_kind.items() if v != "rest"}
    hit = tot = 0
    for e in ev:
        if (e.get("category") or "").upper() != "WORKOUT":
            continue
        d = (e.get("start_date_local") or "")[:10]
        if not d:
            continue
        ed = dt.date.fromisoformat(d)
        if ed < start or ed > today:
            continue
        tot += 1
        if d in run_dates:
            hit += 1
    if tot:
        OUT["kpi"]["adherence"] = f"{round(hit / tot * 100)}%"
        OUT["kpi"]["adherenceDetail"] = f"{hit}/{tot} sessions hit"
    else:
        OUT["kpi"]["adherence"] = "—"
        OUT["kpi"]["adherenceDetail"] = "no plan synced yet"

    # ---------- marathon-pace analytics (live) ----------
    def is_mp(name):
        n = (name or "").lower()
        if "marathon pace" in n or "marathon-pace" in n:
            return True
        return "mp" in re.findall(r"[a-z0-9]+", n)

    mp_runs = []
    for a in acts:
        if a.get("type") not in RUN_TYPES:
            continue
        d = (a.get("start_date_local") or "")[:10]
        if not d or not is_mp(a.get("name")):
            continue
        mp_runs.append((d, a.get("average_heartrate"), a.get("decoupling")))
    mp_runs.sort()
    mp_runs = mp_runs[-6:]
    OUT["mp"] = {
        "labels": [f"MP #{i+1}" for i in range(len(mp_runs))],
        "hr": [round(h) if h else None for _, h, _ in mp_runs],
        "drift": [round(dr, 1) if dr is not None else None for _, _, dr in mp_runs],
    }

    # Habits grid & mobility streak removed — not tracked in intervals.icu
    OUT.pop("habits", None); OUT.pop("streak", None)
    # gear/shoes intentionally removed — no Garmin -> intervals.icu sync available
    OUT.pop("shoes", None)

    with open(os.path.join(HERE, "data.json"), "w") as f:
        json.dump(OUT, f, indent=2, ensure_ascii=False)
    print(f"Wrote data.json — week {weeks_done}/{WEEKS}, {runs_total} runs, "
          f"CTL pts {len(ck)}, VO2 pts {len(keep(vo2))}, "
          f"zones {'yes' if sum(zone_secs)>0 else 'no'}, predicted {m.get('predicted')}")


if __name__ == "__main__":
    main()
