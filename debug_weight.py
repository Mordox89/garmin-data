import garmin_fetch, json

c = garmin_fetch.get_client()

# Test direct
import datetime as dt
end = dt.date.today()
start = end - dt.timedelta(days=120)
raw = c.get_weigh_ins(start.isoformat(), end.isoformat())
print("Type:", type(raw))
if isinstance(raw, dict):
    print("Keys:", list(raw.keys()))
    for k, v in raw.items():
        if isinstance(v, list):
            print(f"  {k}: {len(v)} items, first:", v[0] if v else "empty")
        else:
            print(f"  {k}:", v)
elif isinstance(raw, list):
    print("List length:", len(raw))
    print("First item:", raw[0] if raw else "empty")

input("Druk Enter...")
