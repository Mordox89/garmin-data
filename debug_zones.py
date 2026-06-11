import garmin_fetch, json, datetime as dt

c = garmin_fetch.get_client()
acts = c.get_activities_by_date("2026-06-08", "2026-06-11")
for a in acts[:3]:
    print("Activiteit:", a.get("activityName"), a.get("startTimeLocal","")[:10])
    print("  averageHR:", a.get("averageHR"))
    print("  icu_hr_zone_times:", a.get("icu_hr_zone_times"))
    print("  hrZones:", a.get("hrZones"))
    print("  alle keys:", [k for k in a.keys() if 'zone' in k.lower() or 'hr' in k.lower()])
    print()

input("Druk Enter...")
