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

Pfitzinger workout-types en doelzones (persoonlijk gekalibreerd):
- Recovery / Herstel:      121–145 bpm (64–76% max HR) — hersteldagen, shake-out runs
- Easy / General Aerobic:  140–158 bpm (74–83% max HR) — meeste easy runs
- Endurance / Long Run:    146–164 bpm (77–86% max HR) — lange duurlopen zonder MP
- Marathon Pace (MP):      154–168 bpm (81–88% max HR) — MP-segmenten in long runs
- Lactate Threshold (LT):  166–173 bpm (87–91% max HR) — tempo runs rond LTHR 174
- VO2max / Intervals:      176–184 bpm (93–97% max HR) — korte snelle intervallen

Cadans per trainingstype (beoordeel NOOIT op run-gemiddelde — altijd in context van intensiteit):
- Recovery:        155–165 spm (bereik 150–168)
- Easy/Aerobic:    162–172 spm (bereik 158–175)
- Long Run:        164–174 spm (bereik 160–176)
- Marathon Pace:   168–178 spm (bereik 165–180)
- LT/Tempo:        172–182 spm (bereik 170–185)
- VO2max/Interval: 178–188 spm (bereik 175–190+)
Warm-up en cool-down laps tellen NIET mee voor cadansbeoordeling.

Gebruik ALTIJD bovenstaande zones bij het beoordelen van runs. Categoriseer elke run eerst op type, dan pas op zone-uitvoering.
Plan: Pfitzinger 18/55 hybrid, 55–70 km/week
Voorgeschiedenis: marathon DNF door gluteus/piriformis kramp rechts — niet door conditie
Huidige focus: gluteus-activatie, heupstabiliteit, core (2×/week)
Cadans: bij easy/recovery (>5:30/km) is 170-175 spm normaal en acceptabel. Bij MP-tempo (<5:00/km) doel 178-180 spm. Bij LT/VO2max >180 spm. Gemiddelde cadans over een run is misleidend door warm-up — beoordeel cadans altijd in context van de intensiteit van dat moment.
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

    # ── Recentste run met lap-analyse ──
    run_lines = []
    for r in recent[:3]:
        bal   = f", balans L {r['balance_left']}%" if r.get("balance_left") else ""
        gct   = f", GCT {r['gct_ms']}ms" if r.get("gct_ms") else ""
        vosc  = f", vert.osc {r['vert_osc_cm']}cm" if r.get("vert_osc_cm") else ""
        cad   = f", cadans {r['cadence_spm']} spm" if r.get("cadence_spm") else ""
        hr    = f", HR gem {r['avg_hr']}/max {r['max_hr']}" if r.get("avg_hr") else ""
        load  = f", load {r['load']}" if r.get("load") else ""
        vr   = f", vert.ratio {r['vert_ratio']}%" if r.get("vert_ratio") else ""
        ssl  = f", step speed loss {r['step_speed_loss']}%" if r.get("step_speed_loss") else ""
        strd = f", stride {round(r['stride_cm']/100,2)}m" if r.get("stride_cm") else ""
        run_lines.append(
            f"  {r['date']} — {r['name']}: {r.get('dist_km')} km @ {r.get('pace','—')}"
            f"{hr}{cad}{bal}{gct}{vosc}{vr}{ssl}{strd}{load}"
        )
        # Lap-analyse: filter warm-up/cooldown, analyseer actieve blokken
        splits = r.get("splits", [])
        if splits:
            # Detecteer warm-up (eerste lap(s) met lagere HR) en cooldown (laatste lap)
            all_hrs = [s["hr"] for s in splits if s.get("hr")]
            if all_hrs:
                avg_hr = sum(all_hrs) / len(all_hrs)
                warmup_threshold = avg_hr * 0.88  # laps <88% van gem HR = warm-up/cooldown
                active = [s for s in splits if s.get("hr") and s["hr"] >= warmup_threshold]
                warmup_cool = [s for s in splits if s.get("hr") and s["hr"] < warmup_threshold]

                if active:
                    act_hrs = [s["hr"] for s in active]
                    act_cads = [s["cad"] for s in active if s.get("cad")]
                    # Check HR drift in actieve blokken (teken van vermoeidheid)
                    hr_drift = act_hrs[-1] - act_hrs[0] if len(act_hrs) > 1 else 0
                    drift_str = f" | HR drift actief: {int(hr_drift):+d} bpm ({'vermoeidheid' if hr_drift > 8 else 'stabiel'})" if len(act_hrs) > 1 else ""
                    run_lines.append(
                        f"    Actieve blokken ({len(active)} laps): HR {min(act_hrs)}–{max(act_hrs)} bpm"
                        + (f" | cadans {min(act_cads)}–{max(act_cads)} spm" if act_cads else "")
                        + drift_str
                    )
                if warmup_cool:
                    wc_hrs = [s["hr"] for s in warmup_cool]
                    run_lines.append(f"    Warm-up/cool-down ({len(warmup_cool)} laps): HR {min(wc_hrs)}–{max(wc_hrs)} bpm (niet meegewogen in analyse)")

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

Analyseer als strenge Pfitzinger 18/55 coach. Schrijf als één vloeiende tekst zonder headers.

Zone-uitvoering: Was de run wat Pfitzinger voorschreef? Noem afwijkingen bij naam — een easy run boven 161 bpm is geen easy run.
Loopeconomie: Cadans vs 180 spm doel. Beenbalans rechts >1.5% asymmetrie = piriformis risicosignaal. GCT, vertical ratio, step speed loss als efficiëntie-indicators.
Herstelstatus: Body Battery + slaapkwaliteit (duur + deep% + REM%) + HRV + RHR als één conclusie: hersteld, matig of onderhersteld.
Belasting: ACWR, CTL/ATL/form, ramp rate. Op schema voor week {week}/{total_weeks}?
Race predictor: Beweegt de {predicted} richting het doel? Zo niet: wat ontbreekt?

CRUCIAAL: Als de data ruimte toont (HRV BALANCED + Body Battery >70 + ACWR <1.0 + form positief + slaakscore >75) — adviseer dan actief om die ruimte te benutten met een concreet voorstel. Een coach die alleen beschermt bouwt geen marathonlopers.

Analyseer workouts op lap-niveau: negeer warm-up/cool-down voor de kernanalyse. Bij intervaltraining: beoordeel elk actief blok individueel op HR-drift (vermoeidheid) en herstelsnelheid tussen blokken. Geef feedback in drie vaste blokken: 1) STATUS, 2) DIAGNOSE, 3) DIRECTIEF. Geen locaties. Maximaal 5 bondige alinea's. Sluit af met exact één concrete instructie voor de volgende training met specifieke pace, HR-zone of afstand."""

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
                "Je bent een Pfitzinger 18/55 marathoncoach. Direct en datagedreven. "
                "Schrijf ALLEEN over afwijkingen en risicos. Wat goed ging benoem je niet. "
                "Geen uitleg van zones, geen vergelijkingen, geen bevestigingen dat iets klopt. "
                "Alleen: wat wijkt af, wat betekent dat, wat moet er veranderen. "
                "Analyseer op lap-niveau: negeer warm-up/cooldown. "
                "HR-drift tussen intervalblokken is een vermoeidheidssignaal. "
                "Als data ruimte toont (HRV BALANCED + BB >70 + ACWR <1.0 + form positief): "
                "adviseer actief opschalen met concreet voorstel. "
                "Structuur: 1) STATUS (een zin), 2) AFWIJKINGEN (alleen wat niet klopt), "
                "3) DIRECTIEF (volgende training met specifieke pace/zone/afstand). "
                "Geen locaties. Max 3 alineas van elk max 40 woorden."
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

    with open(DATA_PATH, encoding='utf-8', errors='replace') as f:
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