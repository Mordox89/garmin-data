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
STRENGTH_TYPES = {"strength_training", "functional_strength_training", "cardio_training"}
MOBILITY_TYPES = {"yoga", "pilates", "flexibility", "stretching", "mobility"}

def fetch_activities(client, days=90):
    end = today()
    start = end - dt.timedelta(days=days)
    acts = client.get_activities_by_date(start.isoformat(), end.isoformat())
    return [a for a in acts if (a.get("activityType", {}).get("typeKey") or "").lower() in RUN_TYPES]

def fetch_all_activities(client, days=130):
    """Haal alle activiteiten op inclusief strength en mobility."""
    end = today()
    start = end - dt.timedelta(days=days)
    return client.get_activities_by_date(start.isoformat(), end.isoformat())

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
    result = []
    for i in range(days, -1, -1):
        d_date = today() - dt.timedelta(days=i)
        try:
            raw = client.get_sleep_data(d_date.isoformat())
            if not raw:
                continue
            dto = raw.get("dailySleepDTO") or raw
            if isinstance(dto, list):
                dto = dto[0] if dto else {}
            secs = dto.get("sleepTimeSeconds") or 0
            if not secs:
                continue
            result.append({
                "date":       d_date.isoformat(),
                "duration_h": round(secs / 3600, 1),
                "deep_pct":   round((dto.get("deepSleepSeconds") or 0) / secs * 100, 1),
                "rem_pct":    round((dto.get("remSleepSeconds") or 0) / secs * 100, 1),
                "score":      (dto.get("sleepScores") or {}).get("overall", {}).get("value") if isinstance(dto.get("sleepScores"), dict) else None,
                "hrv_avg":    dto.get("averageRespirationValue"),
            })
        except Exception:
            continue
    return result

def fetch_hrv(client, days=14):
    result = []
    for i in range(days, -1, -1):
        d_date = today() - dt.timedelta(days=i)
        try:
            raw = client.get_hrv_data(d_date.isoformat())
            if not raw:
                continue
            summary = raw.get("hrvSummary") or raw
            if isinstance(summary, list):
                summary = summary[0] if summary else {}
            avg = summary.get("lastNight5MinHigh") or summary.get("lastNight")
            if not avg:
                continue
            result.append({
                "date":      d_date.isoformat(),
                "hrv5":      summary.get("lastNight5MinHigh"),
                "hrv_avg":   summary.get("lastNight"),
                "weekly":    summary.get("weeklyAvg"),
                "status":    summary.get("status"),
                "baseline_low":  summary.get("balancedLow") or 59,
                "baseline_high": summary.get("balancedUpper") or 82,
            })
        except Exception:
            continue
    return result

def fetch_rhr(client, days=14):
    result = []
    for i in range(days, -1, -1):
        d_date = today() - dt.timedelta(days=i)
        try:
            raw = client.get_rhr_day(d_date.isoformat())
            if not raw:
                continue
            val = raw.get("restingHeartRate") or raw.get("value") or (raw.get("allMetrics", {}).get("metricsMap", {}).get("WELLNESS_RESTING_HEART_RATE", [{}])[0].get("value") if isinstance(raw.get("allMetrics"), dict) else None)
            if val:
                result.append({"date": d_date.isoformat(), "rhr": round(val)})
        except Exception:
            continue
    return result

def fetch_weight(client, days=120):
    """Haalt gewichtsmetingen op via dailyWeightSummaries structuur."""
    end = today()
    start = end - dt.timedelta(days=days)
    try:
        data = client.get_weigh_ins(start.isoformat(), end.isoformat())
        summaries = data.get("dailyWeightSummaries") or []
        result = {}
        for day in summaries:
            date = str(day.get("summaryDate") or "")[:10]
            if not date:
                continue
            # Gebruik latestWeight voor de dag
            lw = day.get("latestWeight") or {}
            weight_g = lw.get("weight") or day.get("minWeight")
            if not weight_g:
                continue
            kg = round(float(weight_g) / 1000, 1)
            if kg < 30 or kg > 300:  # sanity check
                continue
            result[date] = kg
        return [{"date": k, "kg": v} for k, v in sorted(result.items())]
    except Exception as e:
        print(f"Gewicht fout: {e}")
        return []

def _parse_training_status(raw):
    """Parse één training_status response naar ctl/atl/acwr/vo2."""
    vo2 = None
    try:
        vo2 = raw["mostRecentVO2Max"]["generic"]["vo2MaxPreciseValue"] or raw["mostRecentVO2Max"]["generic"]["vo2MaxValue"]
    except Exception:
        pass
    atl = ctl = acwr = acwr_status = training_status_str = None
    try:
        devices = raw["mostRecentTrainingStatus"]["latestTrainingStatusData"]
        device_data = list(devices.values())[0]
        atl_dto = device_data.get("acuteTrainingLoadDTO") or {}
        atl  = atl_dto.get("dailyTrainingLoadAcute")
        ctl  = atl_dto.get("dailyTrainingLoadChronic")
        acwr = atl_dto.get("dailyAcuteChronicWorkloadRatio")
        acwr_status = atl_dto.get("acwrStatus")
        training_status_str = device_data.get("trainingStatusFeedbackPhrase")
    except Exception:
        pass
    return atl, ctl, acwr, acwr_status, training_status_str, vo2

def fetch_training_load(client, days=90):
    """Haalt CTL/ATL/ACWR historie op — per dag terug vanaf blokstart."""
    result = []
    start = PLAN_START - dt.timedelta(days=7)
    end = today()
    current = start
    while current <= end:
        try:
            raw = client.get_training_status(current.isoformat())
            atl, ctl, acwr, acwr_status, ts_str, vo2 = _parse_training_status(raw)
            if ctl and atl:
                result.append({
                    "date":            current.isoformat(),
                    "ctl":             round(float(ctl), 1),
                    "atl":             round(float(atl), 1),
                    "form":            round(float(ctl) - float(atl), 1),
                    "acwr":            round(float(acwr), 2) if acwr else None,
                    "acwr_status":     acwr_status,
                    "training_status": ts_str,
                    "vo2":             round(float(vo2), 1) if vo2 else None,
                })
        except Exception:
            pass
        current += dt.timedelta(days=1)
    return result

def fetch_vo2max(client, days=90):
    # Probeer historische VO2max data via get_stats
    result = []
    try:
        # Haal wekelijks VO2max op over het plan
        end = today()
        start = PLAN_START - dt.timedelta(days=7)
        current = start
        while current <= end:
            try:
                raw = client.get_training_status(current.isoformat())
                vo2 = None
                try:
                    vo2 = raw["mostRecentVO2Max"]["generic"]["vo2MaxPreciseValue"]
                except Exception:
                    pass
                if vo2:
                    result.append({"date": current.isoformat(), "vo2": round(float(vo2), 1)})
            except Exception:
                pass
            current += dt.timedelta(days=7)
        # Altijd vandaag toevoegen
        try:
            raw = client.get_training_status(today().isoformat())
            vo2 = raw["mostRecentVO2Max"]["generic"]["vo2MaxPreciseValue"]
            if vo2 and (not result or result[-1]["date"] != today().isoformat()):
                result.append({"date": today().isoformat(), "vo2": round(float(vo2), 1)})
        except Exception:
            pass
    except Exception as e:
        print(f"VO2max fout: {e}")
    return sorted(result, key=lambda x: x["date"])

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
    result = []
    for i in range(days, -1, -1):
        d_date = today() - dt.timedelta(days=i)
        try:
            raw = client.get_stress_data(d_date.isoformat())
            if not raw:
                continue
            avg = raw.get("avgStressLevel")
            if avg and avg > 0:
                result.append({"date": d_date.isoformat(), "avg_stress": avg})
        except Exception:
            continue
    return result

def fetch_scheduled_workouts(client):
    t = today()
    result = []
    months = [(t.year, t.month)]
    if t.month == 12:
        months.append((t.year + 1, 1))
    else:
        months.append((t.year, t.month + 1))
    seen = set()
    for year, month in months:
        try:
            data = client.get_scheduled_workouts(year, month)
            items = data if isinstance(data, list) else (data.get("calendarItems") or data.get("workouts") or [])
            for w in items:
                # Probeer meerdere datumvelden
                date = (w.get("scheduledDate") or w.get("date") or 
                        w.get("startDate") or w.get("calendarDate") or "")
                if not date:
                    continue
                date = str(date)[:10]
                if date in seen:
                    continue
                try:
                    d = dt.date.fromisoformat(date)
                    if d < t or d > t + dt.timedelta(days=7):
                        continue
                except Exception:
                    continue
                seen.add(date)
                # Workout type
                sport = w.get("sportType") or w.get("activityType") or {}
                wtype = sport.get("sportTypeKey") or sport.get("typeKey") or "run" if isinstance(sport, dict) else str(sport).lower()
                result.append({
                    "date": date,
                    "name": w.get("title") or w.get("workoutName") or w.get("name") or "Training",
                    "type": wtype,
                    "desc": w.get("description") or "",
                })
        except Exception as e:
            print(f"Geplande workouts fout ({year}/{month}): {e}")
    return sorted(result, key=lambda x: x.get("date") or "")

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

        # week — alleen runs binnen het plan meenemen
        if date >= PLAN_START:
            wk = week_number(date)
            if wk not in weeks:
                weeks[wk] = {"km": 0, "runs": 0, "load": 0}
            weeks[wk]["km"]   += dist / 1000
            weeks[wk]["runs"] += 1
            weeks[wk]["load"] += load or 0

        # HR zones — gebruik hrTimeInZone_1..5 (seconden per zone per activiteit)
        # Persoonlijke Pfitzinger zones: Recovery 121-145, Easy 140-158, Long/MP 146-168, LT 166-173, VO2 176-184
        # Garmin Z1-Z5 mapping sluit hier het beste op aan
        if date >= PLAN_START:
            for zi in range(5):
                secs = a.get(f"hrTimeInZone_{zi+1}") or 0
                zone_secs[zi] += secs

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
                    balance_left  = s.get("groundContactBalanceLeft")
                    gct           = s.get("groundContactTime")
                    vert_osc      = s.get("verticalOscillation")
                    stride_len    = s.get("strideLength")
                    vert_ratio    = s.get("verticalRatio")
                    step_spd_loss = s.get("stepSpeedLossPercent")
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

        # Bereken Vertical Ratio als niet direct beschikbaar
        vr = None
        try:
            if vert_ratio:
                vr = round(float(vert_ratio), 2)
            elif vert_osc and stride_len and stride_len > 0:
                vr = round((vert_osc * 10) / stride_len * 100, 2)
        except Exception:
            pass

        recent.append({
            "date":           date_str,
            "name":           a.get("activityName") or "Run",
            "activity_id":    act_id,
            "dist_km":        round(dist / 1000, 2),
            "moving_time_s":  round(t),
            "pace":           pace_str(speed),
            "avg_hr":         round(hr)    if hr    else None,
            "max_hr":         round(maxhr) if maxhr else None,
            "cadence_spm":    round(cad)   if cad   else None,
            "load":           round(load)  if load  else None,
            "balance_left":   round(balance_left, 1)  if balance_left   else None,
            "gct_ms":         round(gct, 1)            if gct            else None,
            "vert_osc_cm":    round(vert_osc, 1)       if vert_osc       else None,
            "stride_cm":      round(stride_len, 1)     if stride_len     else None,
            "vert_ratio":     vr,
            "step_speed_loss": round(float(step_spd_loss), 1) if step_spd_loss else None,
            "splits":         splits_out,
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

    # CTL/ATL/form/training_status
    ctl = atl = form = training_status_str = acwr = None
    if training_load:
        last = training_load[-1]
        ctl              = last.get("ctl")
        atl              = last.get("atl")
        form             = last.get("form")
        acwr             = last.get("acwr")
        training_status_str = last.get("training_status")
        # override vo2 met precisere waarde
        if last.get("vo2"):
            vo2 = last.get("vo2")

    return {
        "phase":           f"Week {wk}/{PLAN_WEEKS}",
        "week":            wk,
        "totalWeeks":      PLAN_WEEKS,
        "daysToRace":      days_to_race,
        "predicted":       predicted,
        "predictedRange":  None,
        "vo2":             vo2,
        "updated":         today_d.isoformat(),
        "live":            True,
        "unit":            "km",
        "ctl":             ctl,
        "atl":             atl,
        "form":            form,
        "acwr":            acwr,
        "trainingStatus":  training_status_str,
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
        "acwr":  [d.get("acwr") for d in training_load],
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
    names = ["Recovery", "Easy/Aerobic", "Long Run/MP", "LT/Tempo", "VO2max"]
    colors = ["#2f7d52", "#34e07d", "#45c8e0", "#ffb43a", "#ff5e6c"]
    return [
        {"n": names[i], "v": round(zone_secs[i]/total*100), "c": colors[i]}
        for i in range(5)
    ]

# ── Week7 (komende 7 dagen gepland) ──────────────────────────────────────────
def build_week7(scheduled, completed_dates=None):
    """Bouw weekoverzicht. completed_dates = set van datums waarop een run is uitgevoerd."""
    result = []
    today_d = today()
    done = completed_dates or set()
    for w in scheduled:
        date_str = w.get("date") or ""
        try:
            d = dt.date.fromisoformat(date_str)
            if d == today_d:
                day_name = "Vandaag"
            else:
                day_name = ["Ma","Di","Wo","Do","Vr","Za","Zo"][d.weekday()]
        except:
            day_name = date_str
        est_km = None
        if w.get("estimated_distance_meters"):
            est_km = round(w["estimated_distance_meters"] / 1000, 1)
        elif w.get("estimated_duration_seconds"):
            est_km = None  # geen afstand beschikbaar
        result.append({
            "d":         day_name,
            "date":      date_str,
            "ds":        w.get("name") or w.get("type") or "Training",
            "t":         w.get("type") or "run",
            "km":        f"{est_km} km" if est_km else None,
            "completed": date_str in done,
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

# ── Long runs (langste run per week) ─────────────────────────────────────────
def build_long_runs(raw_acts):
    """Langste run per week — voor long run progressie tracking."""
    runs = [a for a in raw_acts if (a.get("activityType", {}).get("typeKey") or "").lower() in RUN_TYPES]
    week_longest = {}
    for a in runs:
        date_str = (a.get("startTimeLocal") or "")[:10]
        if not date_str:
            continue
        date = dt.date.fromisoformat(date_str)
        if date < PLAN_START:
            continue
        wk = week_number(date)
        dist = (a.get("distance") or 0) / 1000
        hr = a.get("averageHR")
        speed = a.get("averageSpeed") or 0
        t = a.get("duration") or a.get("movingDuration") or 0
        if wk not in week_longest or dist > week_longest[wk]["dist_km"]:
            week_longest[wk] = {
                "week": wk,
                "date": date_str,
                "dist_km": round(dist, 2),
                "pace": pace_str(speed),
                "avg_hr": round(hr) if hr else None,
                "duration_s": round(t),
                "name": a.get("activityName") or "Long Run",
            }
    return [v for _, v in sorted(week_longest.items())]

# ── LT runs (drempel runs op basis van HR > 165) ──────────────────────────────
def build_lt_runs(raw_acts):
    """Runs met gem HR > 165 bpm = threshold/LT werk."""
    runs = [a for a in raw_acts if (a.get("activityType", {}).get("typeKey") or "").lower() in RUN_TYPES]
    lt_runs = []
    for a in sorted(runs, key=lambda x: x.get("startTimeLocal") or ""):
        date_str = (a.get("startTimeLocal") or "")[:10]
        if not date_str:
            continue
        date = dt.date.fromisoformat(date_str)
        if date < PLAN_START:
            continue
        hr = a.get("averageHR") or 0
        if hr < 170:  # alleen echte threshold runs (LTHR 173–174)
            continue
        speed = a.get("averageSpeed") or 0
        lt_runs.append({
            "date": date_str,
            "name": a.get("activityName") or "Run",
            "avg_hr": round(hr),
            "max_hr": round(a.get("maxHR") or 0) if a.get("maxHR") else None,
            "pace": pace_str(speed),
            "dist_km": round((a.get("distance") or 0) / 1000, 2),
        })
    return lt_runs[-8:]  # laatste 8 LT runs

# ── Strength & Mobility tracking ──────────────────────────────────────────────
def build_strength_mobility(all_acts):
    """Strength en mobility sessies per week voor weekoverzicht."""
    result = {}  # week_number -> {strength: [], mobility: []}
    for a in all_acts:
        date_str = (a.get("startTimeLocal") or "")[:10]
        if not date_str:
            continue
        date = dt.date.fromisoformat(date_str)
        if date < PLAN_START:
            continue
        wk = week_number(date)
        type_key = (a.get("activityType", {}).get("typeKey") or "").lower()
        if type_key in STRENGTH_TYPES:
            if wk not in result:
                result[wk] = {"strength": [], "mobility": []}
            result[wk]["strength"].append({
                "date": date_str,
                "name": a.get("activityName") or "Strength",
                "duration_s": round(a.get("duration") or 0),
            })
        elif type_key in MOBILITY_TYPES:
            if wk not in result:
                result[wk] = {"strength": [], "mobility": []}
            result[wk]["mobility"].append({
                "date": date_str,
                "name": a.get("activityName") or "Mobility",
                "duration_s": round(a.get("duration") or 0),
            })
    # Zorg dat alle plan-weken aanwezig zijn
    current_wk = week_number(today())
    for wk in range(1, current_wk + 1):
        if wk not in result:
            result[wk] = {"strength": [], "mobility": []}
    return {str(k): v for k, v in sorted(result.items())}

# ── Slaap kwaliteit score ─────────────────────────────────────────────────────
def build_sleep_quality(sleep_data):
    """Berekent slaapkwaliteitsscore op basis van duur + fases."""
    if not sleep_data:
        return []
    result = []
    for d in sleep_data:
        dur = d.get("duration_h") or 0
        deep = d.get("deep_pct") or 0
        rem  = d.get("rem_pct") or 0
        score = d.get("score")
        # Kwaliteitsscore: duur 0-40 + deep 0-30 + rem 0-30
        dur_score  = min(40, max(0, (dur - 5) / 4 * 40))   # 5-9u -> 0-40
        deep_score = min(30, deep / 20 * 30)               # doel >20% -> max 30
        rem_score  = min(30, rem / 25 * 30)                # doel >25% -> max 30
        quality = round(dur_score + deep_score + rem_score)
        result.append({
            **d,
            "quality_score": score or quality,  # gebruik Garmin score als beschikbaar
            "dur_ok":   dur >= 7,
            "deep_ok":  deep >= 15,
            "rem_ok":   rem >= 20,
        })
    return result

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

    print("Alle activiteiten ophalen voor strength/mobility...")
    all_acts = fetch_all_activities(client, days=130)

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
        "week7":             build_week7(scheduled, completed_dates={a.get("startTimeLocal","")[:10] for a in raw_acts}),
        "ef":                build_ef(recent_acts),
        "recentActivities":  recent_acts,
        "bodyBattery":       body_battery,
        "sleep":             build_sleep_quality(sleep_data),
        "hrv":               hrv_data,
        "rhr":               rhr_data,
        "stress":            stress_data,
        "weight":            weight_data,
        "vo2":               vo2_data,
        "racePredictions":   race_preds,
        "trainingReadiness": readiness,
        "weeks":             {str(k): v for k, v in weeks.items()},
        "longRuns":          build_long_runs(raw_acts),
        "ltRuns":            build_lt_runs(raw_acts),
        "strengthMobility":  build_strength_mobility(all_acts),
    }

    out_path = HERE / "data.json"
    with open(out_path, "w") as f:
        json.dump(OUT, f, indent=2, ensure_ascii=False)
    print(f"data.json geschreven — {len(recent_acts)} recente runs, {len(training_load)} PMC punten")

if __name__ == "__main__":
    main()