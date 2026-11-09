import csv
import os
import time
from datetime import datetime, timedelta, timezone
import requests

"""What the code does is pull from the start time in month batches, and then writes to the csv per month, 
keeping a tab of months processed so if the pull gets cut it saves whatever it did so far"""


url = "https://earthquake.usgs.gov/fdsnws/event/1/"

startime = datetime(2006, 1, 1)
endtime = datetime.now(timezone.utc).replace(tzinfo=None)

minimum_magnitude = 2.5

request_limit = 10000

dataset = "earthquakes_2006_present.csv"
dataset_backup = "earthquakes_progress.txt"

features = ["id", "time", "mag", "magType", "place", "longitude", "latitude", "depth_km", "type", "status", "updated"]

"""sets up parameters to use in pulls"""
def build_params(start, end):
    p = {
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "orderby": "time-asc",
    }
    if minimum_magnitude is not None:
        p["minmagnitude"] = minimum_magnitude
    return p

"""does GET request and tries multiple times if pull fails"""
def get(method, params, fmt):
    for attempt in range(6):
        try:
            r = requests.get(url + method, params={**params, "format": fmt}, timeout=180)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            wait = 5
            print(f"request failed ({e}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Giving up on {method} {params}")

""""takes the pulled json and makes it csv"""
def to_row(feature):
    p = feature["properties"]
    lon, lat, depth = (feature["geometry"]["coordinates"] + [None, None, None])[:3]
    iso = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None
    return {
        "id": feature["id"], "time": iso(p.get("time")), "mag": p.get("mag"),
        "magType": p.get("magType"), "place": p.get("place"),
        "longitude": lon, "latitude": lat, "depth_km": depth,
        "type": p.get("type"), "status": p.get("status"), "updated": iso(p.get("updated")),
    }

"""what fetch window does is just make sure we dont pull too much data from a single month"""
def fetch_window(start, end, writer, seen):
    params = build_params(start, end)
    n = int(get("count", params, "text").text.strip())
    if n == 0:
        return 0
    if n > request_limit:
        mid = start + (end - start) / 2
        print(f"{n} events in {start:%Y-%m-%d}..{end:%Y-%m-%d}, splitting")
        return fetch_window(start, mid, writer, seen) + fetch_window(mid, end, writer, seen)

    features = get("query", params, "geojson").json()["features"]
    added = 0
    for f in features:
        if f["id"] not in seen:
            seen.add(f["id"])
            writer.writerow(to_row(f))
            added += 1
    time.sleep(1)
    return added

""""This just sets month pull lengths"""
def month_windows(start, end):
    cur = start
    while cur < end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield cur, min(nxt, end)
        cur = nxt

"""This reads all the saved rows and gets rid of rows that're potentially incomplete due to pull errors"""
def load_existing():
    if not os.path.exists(dataset):
        return set()
    with open(dataset, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    good = [r for r in rows if r.get("id") and r.get("updated") is not None]
    if len(good) != len(rows):
        print(f"Removing {len(rows) - len(good)} incomplete row(s) from {dataset}")
        with open(dataset, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=features)
            w.writeheader()
            w.writerows(good)
    return {r["id"] for r in good}

"""reloads already saved rows"""
def load_done_months():
    if not os.path.exists(dataset_backup):
        return set()
    with open(dataset_backup, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip()}


def main():
    seen = load_existing()
    done = load_done_months()
    if seen:
        print(f"Resuming: {len(seen)} events already saved, {len(done)} months finished")

    new_file = not os.path.exists(dataset)
    total = 0
    with open(dataset, "a", newline="", encoding="utf-8") as fh, \
         open(dataset_backup, "a", encoding="utf-8") as prog:
        writer = csv.DictWriter(fh, fieldnames=features)
        if new_file:
            writer.writeheader()
        for ws, we in month_windows(startime, endtime):
            key = f"{ws:%Y-%m}"
            if key in done:
                continue
            added = fetch_window(ws, we, writer, seen)
            fh.flush()
            os.fsync(fh.fileno())
            total += added
            print(f"{key}: {added:>6} new events  (this run: {total})")

            if we < endtime:
                prog.write(key + "\n")
                prog.flush()
    print(f"Done. {total} new events this run, {len(seen)} total in {dataset}")


if __name__ == "__main__":
    main()