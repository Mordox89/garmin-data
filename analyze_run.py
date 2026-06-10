#!/usr/bin/env python3
"""
analyze_run.py — AI post-run coaching feedback via Anthropic API.
Reads data.json (written by fetch_data.py) and writes ai_feedback.json.
Runs as second step in the GitHub Actions workflow (after fetch_data.py).

Required secret:  ANTHROPIC_API_KEY
"""

import os, sys, json, datetime as dt

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
HERE      = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "data.json")
OUT_PATH  = os.path.join(HERE, "ai_feedback.json")

ZONE_NAMES = ["Z1 herstel", "Z2 aerobic", "Z3 tempo", "Z4 drempel", "Z5 VO2max"]

ATHLETE_CONTEXT = """
Atletenprofiel:
- Doel: sub-3:00 marathon op 11 oktober 2026 (Eindhoven)
- Race-gewicht doel: 78 kg
- Max HR: 190 BPM | LTHR: 173–174 BPM
- Pfitzinger HR-zones: Z1 <144, Z2 144–160, Z3 161–167, Z4 168–179, Z5 >179
- Plan: Pfitzinger 18/55 hybrid, 55–70 km/week
- Voorgeschiedenis: marathon DNF door gluteus/piriformis kramp (rechts), níet door conditie
- Huidige focus: gluteus-activatie, heupstabiliteit, core (2×/week krachtraining)
- Cadans doelstelling: ~180 spm (nu ~83–85 spm halfcadans = 166–170 spm)
""".strip()


def load_data():
    with open(DATA_PATH) as f:
        return json.load(f)


def fmt_zone_pct(zone_pct):
    if not zone_pct:
        return "geen zone-data"
    parts = []
    for i, pct in enumerate(zone_pct):
        if i < len(ZONE_NAMES):
            parts.append(f"{ZONE_NAMES[i]} {pct}%")
    return ", ".join(parts)


def build_prompt(data):
    meta  = data.get("meta", {})
    kpi   = data.get("kpi", {})
    pmc   = data.get("pmc", {})
    vol   = data.get("volume", {})
    runs  = data.get("recentActivities", [])

    # --- trainingsstatus ---
    week         = meta.get("week", "?")
    total_weeks  = meta.get("totalWeeks", 18)
    days_to_race = meta.get("daysToRace", "?")
    predicted    = meta.get("predicted", "—")
    readiness    = kpi.get("readiness", "—")
    easy_hard    = kpi.get("easyHard", "—")
    ramp         = kpi.get("ramp", "—")
    ramp_note    = kpi.get("rampNote", "—")
    adherence    = kpi.get("adherence", "—")

    ctl_list  = pmc.get("ctl", [])
    atl_list  = pmc.get("atl", [])
    form_list = pmc.get("form", [])
    ctl  = ctl_list[-1]  if ctl_list  else None
    atl  = atl_list[-1]  if atl_list  else None
    form = form_list[-1] if form_list else None

    done     = vol.get("done", [])
    vol_last = done[-1] if done else None
    vol_prev = done[-2] if len(done) >= 2 else None

    weight_list   = data.get("weight", [])
    sleep_list    = data.get("sleep", [])
    soreness_list = data.get("soreness", [])
    ef_list       = data.get("ef", [])
    weight   = weight_list[-1]   if weight_list   else None
    sleep    = sleep_list[-1]    if sleep_list     else None
    soreness = soreness_list[-1] if soreness_list  else None
    ef       = ef_list[-1]       if ef_list        else None
    ef_prev  = ef_list[-2]       if len(ef_list) >= 2 else None

    # --- recente runs ---
    runs_block = ""
    if runs:
        lines = []
        for r in runs:
            zstr = fmt_zone_pct(r.get("zone_pct"))
            dec  = f", decoupling {r['decoupling']}%" if r.get("decoupling") is not None else ""
            cad  = f", cadans {r['cadence_spm']} spm" if r.get("cadence_spm") else ""
            hr   = f", HR gem {r['avg_hr']} / max {r['max_hr']} bpm" if r.get("avg_hr") else ""
            load = f", load {r['load']}" if r.get("load") else ""
            lines.append(
                f"  {r['date']} — {r['name']}: {r.get('dist_km','?')} km "
                f"@ {r.get('pace','—')}{hr}{cad}{load}{dec}\n"
                f"    Zones: {zstr}"
            )
        runs_block = "Recente runs (nieuwste eerst):\n" + "\n".join(lines)
    else:
        runs_block = "Geen individuele run-data beschikbaar (nog geen activiteiten in het blok)."

    # --- upcoming ---
    week7 = data.get("week7", [])
    upcoming = "; ".join(
        f"{w['d']}: {w['ds']} ({w.get('km','—')})"
        for w in week7 if w.get("t") != "rest"
    ) or "geen geplande workouts"

    ef_trend = ""
    if ef and ef_prev:
        diff = round(ef - ef_prev, 3)
        ef_trend = f" (vorige week {ef_prev}, trend {'↑' if diff > 0 else '↓'} {abs(diff):+.3f})"

    status = f"""Trainingsstatus:
- Week {week}/{total_weeks}, {days_to_race} dagen tot de race
- Voorspelde marathon: {predicted}
- Readiness: {readiness} | Form/TSB: {form} (CTL {ctl}, ATL {atl})
- Ramp rate: {ramp} ({ramp_note})
- Volume deze week: {vol_last} km | vorige week: {vol_prev} km
- Easy/hard split (28d): {easy_hard} | Plan adherentie: {adherence}
- Efficiency factor: {ef}{ef_trend}
- Gewicht: {weight} kg | Slaap: {sleep} u | Soreness: {soreness}/5
- Komende workouts: {upcoming}"""

    return f"""{ATHLETE_CONTEXT}

{status}

{runs_block}

Geef post-run coaching feedback. Analyseer:
1. Of de laatste run(s) in de juiste HR-zone zaten (Z2-check of kwaliteitswork naar verwachting)
2. Cadanspatroon — signalen van been-asymmetrie of compensatie (let op grote variatie of afwijking van ~180 spm doel)
3. Efficiency factor trend — verbetert de aerobe motor?
4. Belasting vs herstel in context van de marathon op 11 oktober (form/TSB, ramp, soreness)
Sluit af met precies één concrete aanbeveling voor de volgende training."""


def call_anthropic(prompt):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":          ANTHROPIC_KEY,
            "anthropic-version":  "2023-06-01",
            "content-type":       "application/json",
        },
        json={
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 900,
            "system": (
                "Je bent een elite marathon-trainingscoach. "
                "Geef concrete, datagedreven feedback in het Nederlands. "
                "Wees direct en bondig — maximaal 4 korte alinea's, geen bullet points, geen headers. "
                "Sluit altijd af met exact één concrete aanbeveling voor de volgende training."
            ),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    # Always produce a valid ai_feedback.json — dashboard must never 404
    placeholder = {
        "generated": dt.date.today().isoformat(),
        "feedback":  "AI feedback niet beschikbaar.",
        "status":    "skipped",
    }

    if not ANTHROPIC_KEY:
        print("ANTHROPIC_API_KEY not set — writing placeholder.")
        with open(OUT_PATH, "w") as f:
            json.dump(placeholder, f, indent=2)
        return

    if not os.path.exists(DATA_PATH):
        print("data.json not found — run fetch_data.py first.")
        with open(OUT_PATH, "w") as f:
            json.dump(placeholder, f, indent=2)
        return

    data   = load_data()
    prompt = build_prompt(data)

    print("Calling Anthropic API…")
    try:
        result = call_anthropic(prompt)
    except Exception as e:
        print(f"API call failed: {e}")
        placeholder["feedback"] = f"API fout: {e}"
        placeholder["status"]   = "error"
        with open(OUT_PATH, "w") as f:
            json.dump(placeholder, f, indent=2)
        return

    text = "".join(
        b["text"] for b in result.get("content", []) if b.get("type") == "text"
    ).strip()

    out = {
        "generated": dt.date.today().isoformat(),
        "feedback":  text,
        "status":    "ok",
        "model":     result.get("model", ""),
        "usage":     result.get("usage", {}),
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"ai_feedback.json written — {len(text)} chars")


if __name__ == "__main__":
    main()
