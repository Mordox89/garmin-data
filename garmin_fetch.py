#!/usr/bin/env python3
"""
garmin_fetch.py — haalt trainingsdata op via de Garmin Connect API
en schrijft data.json voor het marathon telemetry dashboard.

Vereisten:
  pip install garminconnect requests

Garmin OAuth tokens: ~/.garminconnect/garmin_tokens.json
(aangemaakt via: uvx --python 3.12 --from git+https://github.com/Taxuspt/garmin_mcp garmin-mcp-auth)

Gebruik:
  python garmin_fetch.py
"""

import os, sys, json, datetime as dt, statistics
from pathlib import Path

try:
    from garminconnect import Garmin
except ImportError:
    sys.exit("pip install garminconnect")

# ── Config ────────────────────────────────────────────────────────────────────
PLAN_START  = dt.date(2026, 6, 8)
RACE_DATE   = dt.date(2026, 10, 11)
PLAN_WEEKS  = 18
HERE        = Path(__file__).parent
TOKEN_PATH  = Path.home() / ".garminconnect"

# Pfitzinger HR zones (persoonlijk, op basis van HRR)
HR_ZONES = [
    ("Recovery",       0,   146),
    ("General aerobic",138, 156),
    ("Long run",       144, 161),
    ("Marathon pace",  157, 169),
    ("LT",             157, 175),
    ("VO2max",         179, 182),
]

RUN_TYPES = {"running", "trail_running", "treadmill_running", "virtual_running"}

# ── Garmin client ─────────────────────────────────────────────────────────────
def get_client():
    token_file = TOKEN_PATH / "garmin_tokens.json"
    if not token_file.exists():
        sys.exit(f"Token niet gevonden: {token_file}\nVoer eerst garmin-mcp-auth uit.")
    client = Garmin()
    client.login(str(TOKEN_PATH))
    return client

# ── Helpers ───────────────────────────────────────────────────────────────────
def pace_str(speed_mps):
    if not speed_mps or speed_mps <= 0:
        return None
    sec_km = 1000 / speed_mps
    return f"{int(sec_km//60)}:{int(sec_km%60):02d}/km"

def hms(seconds):
    s = int(round(seconds))
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"

def monday(d):
    return d - dt.timedelta(days=d.weekday())

def week_number(date):
    start = monday(PLAN_START)
    delta = (date - start).days
    return max(1, delta // 7 + 1)

def today():
    return dt.date.today()

def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)

# ── Data ophalen ──────────────────────────────────────────────────────────────
def fetch_activities(client, days=90):
    end = today()
    start = end - dt.timedelta(days=days)
    acts = client.get_activities_by_date(start.isoformat(), end.isoformat())
    return [a for a in acts if (a.get("activityType", {}).get("typeKey") or "").lower() in RUN_TYPES]

def fetch_splits(client, activity_id):
    try:
        return client.get_activity_split_summaries(activity_id)
    except Exception:
        return {}

def fetch_body_battery(client, days=14):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_body_battery(start.isoformat(), end.isoformat())
        result = []
        for day in data:
            result.append({
                "date":    day.get("date"),
                "charged": day.get("charged"),
                "drained": day.get("drained"),
                "level":   day.get("body_battery_level"),
            })
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"Body battery fout: {e}")
        return []

def fetch_sleep(client, days=14):
    end = today()
    start = end - dt.timedelta(days=days)
    result = []
    try:
        data = client.get_sleep_data(start.isoformat(), end.isoformat())
        daily = data.get("dailySleepDTO") or []
        if isinstance(daily, dict):
            daily = [daily]
        for d in daily:
            date = d.get("calendarDate") or d.get("sleepStartTimestampLocal")
            if date:
                result.append({
                    "date":         str(date)[:10],
                    "duration_h":   round((d.get("sleepTimeSeconds") or 0) / 3600, 1),
                    "deep_pct":     round((d.get("deepSleepSeconds") or 0) / max(d.get("sleepTimeSeconds") or 1, 1) * 100),
                    "rem_pct":      round((d.get("remSleepSeconds") or 0) / max(d.get("sleepTimeSeconds") or 1, 1) * 100),
                    "score":        d.get("sleepScores", {}).get("overall", {}).get("value") if isinstance(d.get("sleepScores"), dict) else None,
                })
    except Exception as e:
        print(f"Slaap fout: {e}")
    return sorted(result, key=lambda x: x["date"])

def fetch_hrv(client, days=14):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_hrv_data(start.isoformat(), end.isoformat())
        result = []
        for d in (data if isinstance(data, list) else [data]):
            date = d.get("startTimestampLocal") or d.get("calendarDate")
            if date:
                result.append({
                    "date":    str(date)[:10],
                    "hrv5":    d.get("lastNight5MinHigh"),
                    "weekly":  d.get("weeklyAvg"),
                    "status":  d.get("hrvStatus"),
                })
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"HRV fout: {e}")
        return []

def fetch_rhr(client, days=14):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_rhr_day(start.isoformat(), end.isoformat())
        result = []
        for d in (data if isinstance(data, list) else [data]):
            date = d.get("calendarDate") or d.get("date")
            val  = d.get("restingHeartRate") or d.get("value")
            if date and val:
                result.append({"date": str(date)[:10], "rhr": val})
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"RHR fout: {e}")
        return []

def fetch_weight(client, days=30):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_weigh_ins(start.isoformat(), end.isoformat())
        entries = data.get("dateWeightList") or []
        result = []
        for d in entries:
            date = d.get("calendarDate")
            val  = d.get("weight")
            if date and val:
                result.append({"date": date, "kg": round(val / 1000, 1)})
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"Gewicht fout: {e}")
        return []

def fetch_training_load(client, days=90):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_training_load_trend(start.isoformat(), end.isoformat())
        result = []
        for d in (data if isinstance(data, list) else []):
            date = d.get("calendarDate")
            if date:
                result.append({
                    "date": date,
                    "ctl":  round(d.get("longTermLoad") or 0, 1),
                    "atl":  round(d.get("shortTermLoad") or 0, 1),
                    "form": round((d.get("longTermLoad") or 0) - (d.get("shortTermLoad") or 0), 1),
                })
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"Training load fout: {e}")
        return []

def fetch_vo2max(client, days=90):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_vo2max_trend(start.isoformat(), end.isoformat())
        result = []
        for d in (data if isinstance(data, list) else []):
            date = d.get("calendarDate")
            val  = d.get("vo2MaxPreciseValue") or d.get("vo2MaxValue")
            if date and val:
                result.append({"date": date, "vo2": round(val, 1)})
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"VO2max fout: {e}")
        return []

def fetch_race_predictions(client):
    try:
        data = client.get_race_predictions()
        if isinstance(data, list):
            data = data[0] if data else {}
        return {
            "5k":   hms(data.get("time5K") or 0) if data.get("time5K") else None,
            "10k":  hms(data.get("time10K") or 0) if data.get("time10K") else None,
            "hm":   hms(data.get("timeHalfMarathon") or 0) if data.get("timeHalfMarathon") else None,
            "fm":   hms(data.get("timeMarathon") or 0) if data.get("timeMarathon") else None,
        }
    except Exception as e:
        print(f"Race predictions fout: {e}")
        return {}

def fetch_training_readiness(client):
    try:
        data = client.get_training_readiness(today().isoformat())
        if isinstance(data, list):
            data = data[-1] if data else {}
        return {
            "score":   data.get("score"),
            "level":   data.get("level"),
            "feedback": data.get("primaryFeedback") or data.get("feedback"),
        }
    except Exception as e:
        print(f"Training readiness fout: {e}")
        return {}

def fetch_stress(client, days=14):
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_stress_data(start.isoformat(), end.isoformat())
        result = []
        for d in (data if isinstance(data, list) else [data]):
            date = d.get("calendarDate")
            avg  = d.get("avgStressLevel")
            if date and avg:
                result.append({"date": date, "avg_stress": avg})
        return sorted(result, key=lambda x: x["date"])
    except Exception as e:
        print(f"Stress fout: {e}")
        return []

def fetch_scheduled_workouts(client):
    start = today()
    end   = start + dt.timedelta(days=7)
    try:
        data = client.get_scheduled_workouts(start.isoformat(), end.isoformat())
        result = []
        for w in (data if isinstance(data, list) else []):
            result.append({
                "date":     w.get("scheduledDate"),
                "name":     w.get("title") or w.get("workoutName"),
                "type":     w.get("sportType", {}).get("sportTypeKey") if isinstance(w.get("sportType"), dict) else None,
                "desc":     w.get("description"),
            })
        return sorted(result, key=lambda x: x.get("date") or "")
    except Exception as e:
        print(f"Geplande workouts fout: {e}")
        return []

# ── Activiteiten verwerken ────────────────────────────────────────────────────
def process_activities(client, raw_acts):
    recent = []
    weeks  = {}
    zone_secs = [0] * 5

    for a in sorted(raw_acts, key=lambda x: x.get("startTimeLocal") or "", reverse=False):
        date_str = (a.get("startTimeLocal") or "")[:10]
        if not date_str:
            continue
        date  = dt.date.fromisoformat(date_str)
        dist  = (a.get("distance") or 0)
        t     = (a.get("duration") or a.get("movingDuration") or 0)
        hr    = a.get("averageHR")
        maxhr = a.get("maxHR")
        cad   = a.get("averageRunningCadenceInStepsPerMinute") or (a.get("averageBikingCadenceInRevPerMinute"))
        load  = a.get("activityTrainingLoad") or a.get("trainingLoad")
        act_id = a.get("activityId")
        name  = a.get("activityName") or "Run"

        # pace
        speed = a.get("averageSpeed") or 0
        pace  = pace_str(speed)

        # week
        wk = week_number(date)
        if wk not in weeks:
            weeks[wk] = {"km": 0, "runs": 0, "load": 0}
        weeks[wk]["km"]   += dist / 1000
        weeks[wk]["runs"] += 1
        weeks[wk]["load"] += load or 0

        # HR zones (Pfitzinger, op basis van gem HR per activiteit)
        if hr:
            if hr < 138:   zone_secs[0] += t
            elif hr < 157: zone_secs[1] += t
            elif hr < 169: zone_secs[2] += t
            elif hr < 179: zone_secs[3] += t
            else:          zone_secs[4] += t

    # Laatste 5 runs met splits
    for a in sorted(raw_acts, key=lambda x: x.get("startTimeLocal") or "", reverse=True)[:5]:
        date_str = (a.get("startTimeLocal") or "")[:10]
        dist     = (a.get("distance") or 0)
        t        = (a.get("duration") or a.get("movingDuration") or 0)
        hr       = a.get("averageHR")
        maxhr    = a.get("maxHR")
        cad      = a.get("averageRunningCadenceInStepsPerMinute")
        load     = a.get("activityTrainingLoad") or a.get("trainingLoad")
        act_id   = a.get("activityId")
        speed    = a.get("averageSpeed") or 0

        # splits en beenbalans ophalen
        balance_left = None
        gct          = None
        vert_osc     = None
        stride_len   = None
        splits_out   = []

        if act_id:
            split_data = fetch_splits(client, act_id)
            summaries  = split_data.get("splitSummaries") or []
            for s in summaries:
                if s.get("splitType") == "INTERVAL_ACTIVE":
                    balance_left = s.get("groundContactBalanceLeft")
                    gct          = s.get("groundContactTime")
                    vert_osc     = s.get("verticalOscillation")
                    stride_len   = s.get("strideLength")
                    break

            # Per-km splits
            laps_raw = client.get_activity_splits(act_id) if act_id else {}
            laps     = (laps_raw.get("lapDTOs") or laps_raw.get("laps") or []) if isinstance(laps_raw, dict) else []
            for lap in laps:
                lap_dist = lap.get("distance") or 0
                if lap_dist < 100:  # sla mini-laps over
                    continue
                lap_t    = lap.get("duration") or 0
                lap_spd  = lap.get("averageSpeed") or 0
                splits_out.append({
                    "km":    round(lap_dist / 1000, 2),
                    "pace":  pace_str(lap_spd),
                    "hr":    lap.get("averageHR"),
                    "cad":   round(lap.get("averageRunCadence") or 0),
                    "power": lap.get("averagePower"),
                    "elev_gain": lap.get("elevationGain"),
                })

        recent.append({
            "date":          date_str,
            "name":          a.get("activityName") or "Run",
            "activity_id":   act_id,
            "dist_km":       round(dist / 1000, 2),
            "moving_time_s": round(t),
            "pace":          pace_str(speed),
            "avg_hr":        round(hr)    if hr    else None,
            "max_hr":        round(maxhr) if maxhr else None,
            "cadence_spm":   round(cad)   if cad   else None,
            "load":          round(load)  if load  else None,
            "balance_left":  round(balance_left, 1) if balance_left else None,
            "gct_ms":        round(gct, 1)          if gct          else None,
            "vert_osc_cm":   round(vert_osc, 1)     if vert_osc     else None,
            "stride_cm":     round(stride_len, 1)   if stride_len   else None,
            "splits":        splits_out,
        })

    return recent, weeks, zone_secs

# ── Meta & KPI ────────────────────────────────────────────────────────────────
def build_meta(race_preds, training_load, vo2_list):
    today_d    = today()
    days_to_race = (RACE_DATE - today_d).days
    wk         = week_number(today_d)

    # Voorspelde marathontijd
    predicted  = race_preds.get("fm", "—")

    # VO2max
    vo2 = vo2_list[-1]["vo2"] if vo2_list else None

    # CTL/ATL/form
    ctl = atl = form = None
    if training_load:
        last = training_load[-1]
        ctl  = last.get("ctl")
        atl  = last.get("atl")
        form = last.get("form")

    return {
        "phase":          f"Week {wk}/{PLAN_WEEKS}",
        "week":           wk,
        "totalWeeks":     PLAN_WEEKS,
        "daysToRace":     days_to_race,
        "predicted":      predicted,
        "predictedRange": None,
        "vo2":            vo2,
        "updated":        today_d.isoformat(),
        "live":           True,
        "unit":           "km",
        "ctl":            ctl,
        "atl":            atl,
        "form":           form,
    }

def build_kpi(weeks, training_load, recent_acts):
    today_d = today()
    wk      = week_number(today_d)
    wk_data = weeks.get(wk, {})

    # Ramp rate (CTL verandering laatste 7 dagen)
    ramp = ramp_note = None
    if len(training_load) >= 7:
        ctl_now  = training_load[-1].get("ctl") or 0
        ctl_week = training_load[-7].get("ctl") or 0
        ramp = round(ctl_now - ctl_week, 1)
        if ramp > 8:    ramp_note = "te hoog"
        elif ramp > 5:  ramp_note = "aan de hoge kant"
        elif ramp >= 3: ramp_note = "optimaal"
        else:           ramp_note = "conservatief"

    # Easy/hard split (28 dagen)
    easy = hard = 0
    cutoff = today_d - dt.timedelta(days=28)
    for a in recent_acts:
        date = dt.date.fromisoformat(a["date"][:10])
        if date < cutoff:
            continue
        hr = a.get("avg_hr") or 0
        if hr < 157:  easy += 1
        else:         hard += 1
    total_runs = easy + hard
    easy_hard = f"{round(easy/total_runs*100)}% easy" if total_runs > 0 else "—"

    return {
        "adherence":      "—",
        "ramp":           ramp,
        "rampNote":       ramp_note,
        "rampSt":         "ok" if ramp and 3 <= ramp <= 8 else "warn",
        "easyHard":       easy_hard,
        "totalVol":       round(sum(w.get("km", 0) for w in weeks.values()), 1),
        "volAvg":         round(statistics.mean([w.get("km", 0) for w in weeks.values()]) if weeks else 0, 1),
        "readiness":      None,  # wordt gevuld vanuit training_readiness
    }

# ── PMC (performance management chart) ───────────────────────────────────────
def build_pmc(training_load):
    dates = [d["date"] for d in training_load]
    return {
        "dates": dates,
        "ctl":   [d["ctl"]  for d in training_load],
        "atl":   [d["atl"]  for d in training_load],
        "form":  [d["form"] for d in training_load],
    }

# ── Volume per week ───────────────────────────────────────────────────────────
def build_volume(weeks):
    sorted_weeks = sorted(weeks.items())
    return {
        "labels": [f"W{w}" for w, _ in sorted_weeks],
        "done":   [round(d.get("km", 0), 1) for _, d in sorted_weeks],
        "plan":   [],  # kan later gevuld worden
    }

# ── HR zones (28 dagen) ───────────────────────────────────────────────────────
def build_zones(zone_secs):
    total = sum(zone_secs)
    if total == 0:
        return []
    names = ["Recovery", "Aerobic", "Tempo", "Threshold", "VO2max"]
    colors = ["#2f7d52", "#34e07d", "#ffb43a", "#ff8a4a", "#ff5e6c"]
    return [
        {"n": names[i], "v": round(zone_secs[i]/total*100), "c": colors[i]}
        for i in range(5)
    ]

# ── Week7 (komende 7 dagen gepland) ──────────────────────────────────────────
def build_week7(scheduled):
    result = []
    for w in scheduled:
        date_str = w.get("date") or ""
        try:
            d = dt.date.fromisoformat(date_str)
            day_name = ["Ma","Di","Wo","Do","Vr","Za","Zo"][d.weekday()]
        except:
            day_name = date_str
        result.append({
            "d":  day_name,
            "ds": w.get("name") or w.get("type") or "Training",
            "t":  w.get("type") or "run",
            "km": None,
        })
    return result

# ── Efficiency factor ─────────────────────────────────────────────────────────
def build_ef(recent_acts):
    ef_list = []
    for a in sorted(recent_acts, key=lambda x: x["date"]):
        hr    = a.get("avg_hr")
        power = None
        if a.get("splits"):
            powers = [s.get("power") for s in a["splits"] if s.get("power")]
            if powers:
                power = statistics.mean(powers)
        if hr and power and hr > 0:
            ef_list.append(round(power / hr, 3))
    return ef_list

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Verbinding maken met Garmin Connect...")
    client = get_client()
    print("Verbonden!")

    print("Activiteiten ophalen...")
    raw_acts = fetch_activities(client, days=130)
    print(f"  {len(raw_acts)} runs gevonden")

    print("Splits & beenbalans ophalen voor laatste 5 runs...")
    recent_acts, weeks, zone_secs = process_activities(client, raw_acts)

    print("Hersteldata ophalen...")
    body_battery = fetch_body_battery(client, days=14)
    sleep_data   = fetch_sleep(client, days=14)
    hrv_data     = fetch_hrv(client, days=14)
    rhr_data     = fetch_rhr(client, days=14)
    stress_data  = fetch_stress(client, days=14)

    print("Conditiedata ophalen...")
    training_load  = fetch_training_load(client, days=90)
    vo2_data       = fetch_vo2max(client, days=90)
    race_preds     = fetch_race_predictions(client)
    readiness      = fetch_training_readiness(client)
    weight_data    = fetch_weight(client, days=30)
    scheduled      = fetch_scheduled_workouts(client)

    print("Data samenstellen...")
    meta  = build_meta(race_preds, training_load, vo2_data)
    kpi   = build_kpi(weeks, training_load, recent_acts)
    kpi["readiness"] = readiness.get("score")

    OUT = {
        "meta":              meta,
        "kpi":               kpi,
        "pmc":               build_pmc(training_load),
        "volume":            build_volume(weeks),
        "zones":             build_zones(zone_secs),
        "week7":             build_week7(scheduled),
        "ef":                build_ef(recent_acts),
        "recentActivities":  recent_acts,
        "bodyBattery":       body_battery,
        "sleep":             sleep_data,
        "hrv":               hrv_data,
        "rhr":               rhr_data,
        "stress":            stress_data,
        "weight":            weight_data,
        "vo2":               vo2_data,
        "racePredictions":   race_preds,
        "trainingReadiness": readiness,
        "weeks":             {str(k): v for k, v in weeks.items()},
    }

    out_path = HERE / "data.json"
    with open(out_path, "w") as f:
        json.dump(OUT, f, indent=2, ensure_ascii=False)
    print(f"data.json geschreven — {len(recent_acts)} recente runs, {len(training_load)} PMC punten")

if __name__ == "__main__":
    main()
