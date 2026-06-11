import garmin_fetch, json, traceback

try:
    c = garmin_fetch.get_client()
    result = garmin_fetch.fetch_training_load(c)
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"FOUT: {e}")
    traceback.print_exc()

input("Druk Enter om af te sluiten...")
