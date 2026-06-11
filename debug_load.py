import garmin_fetch, json
c = garmin_fetch.get_client()
result = garmin_fetch.fetch_training_load(c)
print(json.dumps(result, indent=2))
