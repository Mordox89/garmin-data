#!/usr/bin/env python3
"""
analyze_run.py — AI post-run coaching feedback via Anthropic API.
Leest data.json (geschreven door garmin_fetch.py) en schrijft ai_feedback.json.

Vereiste secret: ANTHROPIC_API_KEY (GitHub Actions) of lokale env var
"""

import os, sys, json, datetime as dt
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
HERE      = Path(__file__).parent
DATA_PATH = HERE / "data.json"
OUT_PATH  = HERE / "ai_feedback.json"

ATHLETE_CONTEXT = """
Atletenprofiel:
- Doel: zo snel mogelijk lopen op 11 oktober 2026 — maximale prestatie op basis van de data
- Max HR: 190 BPM | LTHR: 173–174 BPM

Pfitzinger HR-zones (persoonlijk, op basis van HRR):
- Recovery:        <146 bpm
- General aerobic: 138–156 bpm
- Long run:        144–161 bpm
- Marathon pace:   157–169 bpm
- Lactate threshold: 157–175 bpm
- VO2max intervals: 179–182 bpm

Gebruik ALTIJD bovenstaande zones bij het beoordelen van runs.
Plan: Pfitzinger 18/55 hybrid, 55–70 km/week
Voorgeschiedenis: marathon DNF door gluteus/piriformis kramp rechts — niet door conditie
Huidige focus: gluteus-activatie, heupstabiliteit, core (2×/week)
Cadans doelstelling: ~180 spm
Beenbalans doel: <2% asymmetrie (huidig: ~52% links = 4% afwijking)
""".strip()


def hms(s):
    s = int(s)
    return f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}"


def build_prompt(data):
    meta       = data.get("meta", {})
    kpi        = data.get("kpi", {})
    pmc        = data.get("pmc", {})
    recent     = data.get("recentActivities", [])
    bb         = data.get("bodyBattery", [])
    sleep_d    = data.get("sleep", [])
    hrv_d      = data.get("hrv", [])
    rhr_d      = data.get("rhr", [])
    readiness  = data.get("trainingReadiness", {})
    race_preds = data.get("racePredictions", {})
    weight_d   = data.get("weight", [])
    stress_d   = data.get("stress", [])
    scheduled  = data.get("week7", [])

    # ── Meta ──
    week        = meta.get("week", "?")
    total_weeks = meta.get("totalWeeks", 18)
    days_to_race = meta.get("daysToRace", "?")
    predicted   = race_preds.get("fm") or meta.get("predicted", "—")
    ctl  = meta.get("ctl")
    atl  = meta.get("atl")
    form = meta.get("form")
    vo2  = meta.get("vo2")

    # ── KPI ──
    ramp      = kpi.get("ramp")
    ramp_note = kpi.get("rampNote")
    easy_hard = kpi.get("easyHard", "—")

    # ── Herstel ──
    bb_today    = bb[-1] if bb else None
    sleep_today = sleep_d[-1] if sleep_d else None
    hrv_today   = hrv_d[-1] if hrv_d else None
    rhr_today   = rhr_d[-1] if rhr_d else None
    stress_avg  = stress_d[-1].get("avg_stress") if stress_d else None
    weight      = weight_d[-1].get("kg") if weight_d else None

    # ── Race predictions ──
    pred_str = " | ".join(f"{k.upper()}: {v}" for k, v in race_preds.items() if v)

    # ── Recentste run ──
    run_lines = []
    for r in recent[:3]:
        bal   = f", balans L {r['balance_left']}%" if r.get("balance_left") else ""
        gct   = f", GCT {r['gct_ms']}ms" if r.get("gct_ms") else ""
        vosc  = f", vert.osc {r['vert_osc_cm']}cm" if r.get("vert_osc_cm") else ""
        cad   = f", cadans {r['cadence_spm']} spm" if r.get("cadence_spm") else ""
        hr    = f", HR gem {r['avg_hr']}/max {r['max_hr']}" if r.get("avg_hr") else ""
        load  = f", load {r['load']}" if r.get("load") else ""
        run_lines.append(
            f"  {r['date']} — {r['name']}: {r.get('dist_km')} km @ {r.get('pace','—')}"
            f"{hr}{cad}{bal}{gct}{vosc}{load}"
        )
        # splits samenvatting
        splits = r.get("splits", [])
        if splits:
            hr_vals = [s["hr"] for s in splits if s.get("hr")]
            cad_vals = [s["cad"] for s in splits if s.get("cad")]
            if hr_vals:
                run_lines.append(
                    f"    Splits HR: min {min(hr_vals)} → max {max(hr_vals)} bpm"
                    + (f" | cadans: {min(cad_vals)}–{max(cad_vals)} spm" if cad_vals else "")
                )

    # ── Geplande workouts ──
    upcoming = "; ".join(
        f"{w['d']}: {w['ds']}" for w in scheduled
    ) or "geen geplande workouts"

    prompt = f"""{ATHLETE_CONTEXT}

Trainingsstatus:
- Week {week}/{total_weeks}, {days_to_race} dagen tot de race
- Garmin race predictions: {pred_str or '—'}
- VO2max: {vo2} | CTL: {ctl} | ATL: {atl} | Form/TSB: {form}
- Ramp rate: {ramp} ({ramp_note}) | Easy/hard 28d: {easy_hard}

Herstel vandaag:
- Body Battery: {bb_today.get('charged')} opgeladen / {bb_today.get('drained')} verbruikt ({bb_today.get('level')}) {f"| Slaap: {sleep_today.get('duration_h')}u (deep {sleep_today.get('deep_pct')}%, REM {sleep_today.get('rem_pct')}%)" if sleep_today else ''} {f"| HRV: {hrv_today.get('hrv5')} (status: {hrv_today.get('status')})" if hrv_today else ''} {f"| RHR: {rhr_today.get('rhr')} bpm" if rhr_today else ''} {f"| Stress: {stress_avg}" if stress_avg else ''} {f"| Gewicht: {weight} kg" if weight else ''}
- Training readiness: {readiness.get('score')} ({readiness.get('level')}) — {readiness.get('feedback') or '—'}

Recente runs (nieuwste eerst):
{chr(10).join(run_lines) or '  Geen recente runs beschikbaar'}

Komende week: {upcoming}

Analyseer als Pfitzinger-coach:
1. Zone-uitvoering: zat de laatste run in de juiste Pfitzinger-zone?
2. Beenbalans & loopeconomie: L/R balans trend, GCT, vertical oscillation, cadans
3. Herstelstatus: Body Battery + slaap + HRV + RHR in samenhang
4. Belasting: CTL/ATL/form richting 11 oktober — op schema?
Geen locaties vermelden. Sluit af met exact één concrete instructie voor de volgende geplande training."""

    return prompt


def call_anthropic(prompt):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-6",
            "max_tokens": 900,
            "system": (
                "Je bent een professionele marathon-trainingscoach gespecialiseerd in Pfitzinger 18/55. "
                "Je toon is direct, technisch en resultaatgericht — geen aanmoedigingen, geen complimenten tenzij data het verdient. "
                "Schrijf in het Nederlands. Maximaal 4 alinea's, geen bullet points, geen headers. "
                "Sluit altijd af met exact één concrete instructie voor de volgende geplande training."
            ),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    placeholder = {
        "generated": dt.date.today().isoformat(),
        "feedback":  "AI feedback niet beschikbaar.",
        "status":    "skipped",
    }

    if not ANTHROPIC_KEY:
        print("ANTHROPIC_API_KEY niet ingesteld — placeholder schrijven.")
        with open(OUT_PATH, "w") as f:
            json.dump(placeholder, f, indent=2)
        return

    if not DATA_PATH.exists():
        print("data.json niet gevonden — voer garmin_fetch.py eerst uit.")
        with open(OUT_PATH, "w") as f:
            json.dump(placeholder, f, indent=2)
        return

    with open(DATA_PATH) as f:
        data = json.load(f)

    prompt = build_prompt(data)
    print("Anthropic API aanroepen...")

    try:
        result = call_anthropic(prompt)
    except Exception as e:
        print(f"API fout: {e}")
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

    print(f"ai_feedback.json geschreven — {len(text)} chars")


if __name__ == "__main__":
    main()
