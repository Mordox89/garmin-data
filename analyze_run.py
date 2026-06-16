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
- Doel: zo snel mogelijk lopen op 11 oktober 2026 — maximale prestatie
- Plan: Pfitzinger 18/55 hybrid
- Max HR 190 | LTHR 173–174

Pfitzinger zones:
- Recovery: <146 bpm
- General aerobic: 138–156 bpm
- Long run: 144–161 bpm
- Marathon pace: 157–169 bpm
- Lactate threshold: 157–175 bpm
- VO2max intervals: 179–182 bpm

Cadans per type:
- Recovery/Easy (>5:30/km): 155–172 spm normaal
- Long run: 164–174 spm
- MP (<5:00/km): 168–180 spm
- LT/VO2max: 172–188+ spm
Gemiddelde cadans per run is misleidend door warm-up — beoordeel altijd in context van intensiteit.

Voorgeschiedenis: marathon DNF door rechter gluteus/piriformis kramp — niet door conditie
Beenbalans doel: <2% asymmetrie (huidig ~52% links = structurele afwijking)
Huidige focus: gluteus-activatie rechts, heupstabiliteit, core (2x/week)
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
                    # Filter strides uit (plotselinge HR-sprong >15 bpm in laatste 1-2 laps)
                    core_hrs = list(act_hrs)
                    while len(core_hrs) > 2 and core_hrs[-1] - core_hrs[-2] > 12:
                        core_hrs.pop()  # verwijder strides-laps
                    hr_drift = core_hrs[-1] - core_hrs[0] if len(core_hrs) > 1 else 0
                    strides_detected = len(core_hrs) < len(act_hrs)
                    drift_note = 'vermoeidheid' if hr_drift > 8 else 'stabiel'
                    if strides_detected:
                        drift_note += f', {len(act_hrs)-len(core_hrs)} strides-laps uitgefilterd'
                    drift_str = f" | HR drift kern: {int(hr_drift):+d} bpm ({drift_note})" if len(core_hrs) > 1 else ""
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
- Vandaag: {dt.date.today().strftime('%A %d %B %Y')} (gebruik dit als referentiedatum voor dag-aanduidingen)
- Week {week}/{total_weeks}, {days_to_race} dagen tot de race
- Garmin race predictions: {pred_str or '—'}
- VO2max: {vo2} | CTL: {ctl} | ATL: {atl} | Form/TSB: {form}
- Ramp rate: {ramp} ({ramp_note}) | Easy/hard 28d: {easy_hard}

Herstel vandaag:
- Body Battery: +{bb_today.get('charged')} opgeladen vannacht, -{bb_today.get('drained')} verbruikt vandaag, netto {bb_today.get('net', '?')}, status {bb_today.get('level')} (NB: charged/drained zijn dagcijfers, niet het actuele BB-niveau)
- Slaap: {f"{sleep_today.get('duration_h')}u (deep {sleep_today.get('deep_pct')}%, REM {sleep_today.get('rem_pct')}%, score {sleep_today.get('quality_score','?')})" if sleep_today else 'geen data'}
- HRV: {f"{hrv_today.get('hrv5')} ms 5min max, weekly avg {hrv_today.get('weekly','?')} ms, status {hrv_today.get('status')}" if hrv_today else 'geen data'}
- RHR: {f"{rhr_today.get('rhr')} bpm" if rhr_today else 'geen data'} | Stress: {stress_avg or 'geen data'} | Gewicht: {f"{weight} kg" if weight else 'geen data'}
- Training readiness: {readiness.get('score')} ({readiness.get('level')}) — {readiness.get('feedback') or '—'}

Recente runs (nieuwste eerst):
{chr(10).join(run_lines) or '  Geen recente runs beschikbaar'}

Komende week: {upcoming}

Geef een complete Pfitzinger coaching analyse:
1. Zone-uitvoering — zat de run in de juiste Pfitzinger-zone voor dit type training? Analyseer actieve laps apart van warm-up/cooldown. Strides aan het eind van een run apart beoordelen, niet als HR-drift.
2. Beenbalans & loopeconomie — L/R balans trend, GCT, vertical oscillation, vertical ratio, stride length, cadans in context van intensiteit (niet gemiddelde cadans over hele run)
3. Herstelstatus — Body Battery (actueel niveau) + slaapkwaliteit (duur + deep% + REM%) + HRV status en trend vs baseline + training readiness — trek een samenhangend oordeel
4. Belasting — ACWR, CTL/ATL/form trend, training status — is de opbouw verantwoord voor deze fase van het Pfitzinger-blok?
5. Als de data ruimte toont (HRV BALANCED, goede slaap, BB hoog, ACWR <1.2): adviseer actief om die ruimte te benutten met een concreet voorstel

Geen locaties vermelden. Gebruik concrete cijfers uit de data. Sluit af met één concrete instructie voor de volgende geplande training."""

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
            "max_tokens": 1500,
            "system": "Je bent een professionele Pfitzinger 18/55 marathoncoach die Garmin-data analyseert. Schrijf in het Nederlands. Geef een complete, datagedreven analyse zoals een elite coach dat na een training zou doen. Wees direct maar grondig — benoem wat goed ging én wat niet. Gebruik concrete cijfers uit de data. Geen locaties vermelden. Sluit af met één concrete instructie voor de volgende training.",
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
