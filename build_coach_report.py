#!/usr/bin/env python3
"""
build_coach_report.py — genereert een gestructureerd AI coaching rapport
op basis van data.json en schrijft het terug als data["coachReport"].

Gebruik:
  python build_coach_report.py

Vereist:
  pip install anthropic
  ANTHROPIC_API_KEY in omgevingsvariabelen (of .env)
"""

import json, os, sys
from pathlib import Path
from datetime import datetime

try:
    import anthropic
except ImportError:
    sys.exit("pip install anthropic")

HERE = Path(__file__).parent
DATA_PATH = HERE / "data.json"

SYSTEM_PROMPT = """Je bent een expert marathon coach gespecialiseerd in Pfitzinger-methodiek.
Je analyseert hardloopdata en geeft gestructureerde feedback in JSON.

Reageer ALTIJD en UITSLUITEND met valide JSON in exact dit formaat — geen tekst ervoor of erna:

{
  "generatedAt": "<ISO datetime>",
  "lastRun": {
    "verdict": "goed" | "voldoende" | "aandacht" | "zorg",
    "summary": "<1-2 zinnen samenvatting van de run>"
  },
  "sections": [
    {
      "id": "zone",
      "title": "Zone-uitvoering",
      "icon": "heart-rate-monitor",
      "verdict": "goed" | "voldoende" | "aandacht" | "zorg",
      "metrics": [
        { "label": "<naam>", "value": "<waarde>", "unit": "<eenheid of null>", "delta": "<vergelijking met vorige run of null>", "deltaDir": "pos" | "neg" | "neu" | null }
      ],
      "insight": "<2-3 zinnen coaching inzicht>"
    }
  ],
  "conclusion": {
    "verdict": "goed" | "voldoende" | "aandacht" | "zorg",
    "text": "<concrete aanbeveling voor de komende 24-48 uur>"
  },
  "nextSessionAdvice": "<wat te doen bij de volgende training>"
}

Gebruik altijd deze 4 secties in volgorde: zone, economy, balance, recovery.
Secties:
- zone: HR-zone naleving, drift, tempo
- economy: GCT, vertical ratio, step speed loss, cadans
- balance: beenbalans links%, asymmetrie trend, piriformis risico
- recovery: HRV status, slaap, body battery, form/TSB

Gebruik Nederlandse tekst voor alle waarden. Wees direct en concreet — geen algemeenheden.
Pfitzinger context: GA zone 138-156 bpm, LT zone 157-175 bpm, marathon pace 154-168 bpm."""


def build_prompt(data: dict) -> str:
    recent = data.get("recentActivities", [])
    last = recent[0] if recent else {}
    prev = recent[1] if len(recent) > 1 else {}
    prev2 = recent[2] if len(recent) > 2 else {}

    hrv = data.get("hrv", [])
    hrv_latest = hrv[-1] if hrv else {}

    sleep = data.get("sleep", {})
    bb = data.get("bodyBattery", [])
    bb_latest = bb[-1] if bb else {}

    pmc = data.get("pmc", {})
    tl_latest = {
        "ctl":  pmc.get("ctl",  [None])[-1],
        "atl":  pmc.get("atl",  [None])[-1],
        "form": pmc.get("form", [None])[-1],
        "acwr": pmc.get("acwr", [None])[-1],
    }

    piri = data.get("piriformisRisk", {})
    sleep_debt = data.get("sleepDebt", {})

    prompt = f"""Analyseer deze Garmin trainingsdata en genereer een coach rapport.

## Laatste run
Datum: {last.get('date')}
Naam: {last.get('name')}
Afstand: {last.get('dist_km')} km
Tempo: {last.get('pace')} /km
Gem HR: {last.get('avg_hr')} bpm | Max HR: {last.get('max_hr')} bpm
Cadans: {last.get('cadence_spm')} spm
Trainingsload: {last.get('load')}

Beenbalans links: {last.get('balance_left')}%
GCT: {last.get('gct_ms')} ms
Vertical oscillatie: {last.get('vert_osc_cm')} cm
Vertical ratio: {last.get('vert_ratio')}%
Step speed loss: {last.get('step_speed_loss')}%
Stride length: {last.get('stride_cm')} cm

## Vorige runs (voor trend)
Run -1 ({prev.get('date')}): {prev.get('dist_km')}km @ {prev.get('pace')}, HR {prev.get('avg_hr')}, balans {prev.get('balance_left')}%, GCT {prev.get('gct_ms')}ms, vert.ratio {prev.get('vert_ratio')}%, step loss {prev.get('step_speed_loss')}%
Run -2 ({prev2.get('date')}): {prev2.get('dist_km')}km @ {prev2.get('pace')}, HR {prev2.get('avg_hr')}, balans {prev2.get('balance_left')}%, GCT {prev2.get('gct_ms')}ms, vert.ratio {prev2.get('vert_ratio')}%, step loss {prev2.get('step_speed_loss')}%

## Herstelstatus
HRV status: {hrv_latest.get('status')} | HRV waarde: {hrv_latest.get('lastNight5MinHigh')} ms
Slaap gemiddeld (7d): {sleep_debt.get('avg_hours')}u/nacht | Slaapschuld: {sleep_debt.get('debt_hours')}u
Body battery: {bb_latest.get('value') if isinstance(bb_latest, dict) else bb_latest}
Piriformis risico: {piri.get('score')}/100 ({piri.get('level')}) — factoren: {', '.join(piri.get('factors', [])) or 'geen'}

## Conditie (PMC)
CTL (fitness): {tl_latest.get('ctl')}
ATL (vermoeidheid): {tl_latest.get('atl')}
Form/TSB: {tl_latest.get('form')}
ACWR: {tl_latest.get('acwr')}

Genereer nu het JSON rapport."""

    return prompt


def main():
    if not DATA_PATH.exists():
        sys.exit(f"data.json niet gevonden: {DATA_PATH}")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Probeer .env
        env_file = HERE / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY niet gevonden. Stel in als omgevingsvariabele of in .env")

    with open(DATA_PATH, encoding="utf-8-sig", errors="replace") as f:
        data = json.load(f)

    recent = data.get("recentActivities", [])
    if not recent:
        print("Geen recente activiteiten gevonden — coach rapport overgeslagen.")
        return

    print(f"Coach rapport genereren voor: {recent[0].get('date')} — {recent[0].get('name')}...")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(data)}],
    )

    raw = message.content[0].text.strip()

    # Strip eventuele markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        report = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse fout: {e}")
        print("Raw output:", raw[:500])
        sys.exit(1)

    report["generatedAt"] = datetime.now().isoformat()
    data["coachReport"] = report

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    verdict = report.get("lastRun", {}).get("verdict", "—")
    print(f"Coach rapport geschreven — verdict: {verdict}")


if __name__ == "__main__":
    main()