#!/usr/bin/env python3
"""
garmin_sync.py

Fetches Garmin activities since the last sync, parses them into structured
JSON, appends to local plain-text tracking files, commits + pushes to git,
and POSTs to a Cloudflare Worker endpoint (stubbed).

Dependencies:
    pip install garminconnect python-dotenv requests

Config via .env file (see .env.example) or environment variables.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from garminconnect import Garmin

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GARMIN_EMAIL = os.environ["GARMIN_EMAIL"]
GARMIN_PASSWORD = os.environ["GARMIN_PASSWORD"]

# Directory containing the JSON tracking files (should be a git repo)
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))

# On the very first run (no .last_sync file), fetch this many days back.
INITIAL_LOOKBACK_DAYS = int(os.environ.get("INITIAL_LOOKBACK_DAYS", "30"))

# Cloudflare Worker endpoint -- set to empty string to skip upload
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "")
CF_API_KEY = os.environ.get("CF_API_KEY", "")

# Stores the datetime of the last successful sync
LAST_SYNC_FILE = DATA_DIR / ".last_sync"

# ---------------------------------------------------------------------------
# Activity type routing
# ---------------------------------------------------------------------------

# Maps Garmin typeKey values to local file names (without .json extension)
ACTIVITY_TYPE_MAP = {
    "strength_training": "strength",
    "running": "runs",
    "walking": "walks",
    "cycling": "bike_rides",
    "mountain_biking": "bike_rides",
    "hiking": "hikes",
    "trail_running": "runs",
}

with open("exercise_name_mapping.json", 'r', encoding='utf-8') as f:
    EXERCISE_NAME_MAPPING = json.load(f)

# ---------------------------------------------------------------------------
# Last sync helpers
# ---------------------------------------------------------------------------

def read_last_sync() -> datetime | None:
    if not LAST_SYNC_FILE.exists():
        return None
    text = LAST_SYNC_FILE.read_text().strip()
    return datetime.fromisoformat(text)


def write_last_sync(dt: datetime) -> None:
    LAST_SYNC_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_SYNC_FILE.write_text(dt.isoformat())


# ---------------------------------------------------------------------------
# Garmin client
# ---------------------------------------------------------------------------

def get_garmin_client() -> Garmin:
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    return client


def fetch_activities(client: Garmin, since: datetime) -> list[dict]:
    start_date = since.strftime("%Y-%m-%d")
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"  Fetching activities from {start_date} to {end_date}")
    return client.get_activities_by_date(start_date, end_date)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_strength(activity: dict, client: Garmin) -> dict | None:
    """Parse a strength training activity into the session/sets schema."""
    activity_id = activity["activityId"]

    try:
        details = client.get_activity_exercise_sets(activity_id)
    except Exception as e:
        print(f"  Warning: could not fetch exercise sets for {activity_id}: {e}")
        return None

    start_time = activity.get("startTimeLocal") or activity.get("startTimeGMT", "")
    lifts: dict[str, dict] = {}

    for ex_set in details.get("exerciseSets", []):
        if ex_set.get("setType") != "ACTIVE":
            continue

        exercise_info = ex_set.get("exercises", [{}])[0]
        exercise_name = (
            exercise_info.get("displayName")
            or exercise_info.get("name")
            or exercise_info.get("category")
            or "unknown"
        ).strip()
        exercise_name = EXERCISE_NAME_MAPPING.get(exercise_name, exercise_name)

        weight_g = ex_set.get("weight")
        set_entry = {
            "reps": ex_set.get("repetitionCount"),
            "weight_lbs": round(weight_g * 0.00220462, 1) if weight_g is not None else None,
            # "duration_s": ex_set.get("duration"),
        }

        if exercise_name not in lifts:
            lifts[exercise_name] = {"exercise": exercise_name, "sets": []}
        lifts[exercise_name]["sets"].append(set_entry)

    return {
        "date": start_time,
        "duration_min": round((activity.get("duration", 0) or 0) / 60, 1),
        "notes": activity.get("description") or "",
        "lifts": list(lifts.values()),
        "activity_id": activity.get("activityId"),
        "type": "strength",
    }


def parse_cardio(activity: dict, activity_type: str) -> dict:
    """Parse a cardio activity (run/walk/bike/hike) into a summary record."""
    start_time = activity.get("startTimeLocal") or activity.get("startTimeGMT", "")
    distance_m = activity.get("distance") or 0
    duration_s = activity.get("duration") or 0
    avg_speed_mps = activity.get("averageSpeed")

    pace_min_per_mile = None
    avg_speed_mph = None
    if avg_speed_mps and avg_speed_mps > 0:
        avg_speed_mph = round(avg_speed_mps * 2.23694, 2)
        if activity_type in ("runs", "walks", "hikes"):
            pace_min_per_mile = round(60 / avg_speed_mph, 2)

    elevation_gain_m = activity.get("elevationGain")

    return {
        "date": start_time,
        "duration_min": round(duration_s / 60, 1),
        "distance_mi": round(distance_m * 0.000621371, 2),
        "avg_hr": activity.get("averageHR"),
        "max_hr": activity.get("maxHR"),
        "calories": activity.get("calories"),
        "avg_speed_mph": avg_speed_mph,
        "pace_min_per_mile": pace_min_per_mile,
        "elevation_gain_ft": round(elevation_gain_m * 3.28084, 0) if elevation_gain_m else None,
        "notes": activity.get("description") or "",
        "activity_id": activity.get("activityId"),
        "type": activity_type,
    }


def parse_activity(activity: dict, client: Garmin) -> tuple[str, dict] | None:
    """Returns (file_key, parsed_record) or None if activity type is not tracked."""
    type_key = (
        activity.get("activityType", {}).get("typeKey", "")
        or activity.get("activityTypeDTO", {}).get("typeKey", "")
    ).lower()

    file_key = ACTIVITY_TYPE_MAP.get(type_key)
    if file_key is None:
        return None

    print(f"  Parsing {type_key} ({activity['activityId']}) -> {file_key}")

    if file_key == "strength":
        record = parse_strength(activity, client)
        if record is None:
            return None
    else:
        record = parse_cardio(activity, file_key)

    return file_key, record


# ---------------------------------------------------------------------------
# Local JSON file management
# ---------------------------------------------------------------------------

def load_json_file(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def save_json_file(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_new_records(new_by_type: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """
    Appends new records to the appropriate JSON files.
    Returns dict of {file_key: [newly_appended_records]}.
    """
    appended: dict[str, list[dict]] = {}

    for file_key, new_records in new_by_type.items():
        path = DATA_DIR / f"{file_key}.json"
        existing = load_json_file(path)
        existing_ids = {r["activity_id"] for r in existing}

        to_add = [r for r in new_records if r["activity_id"] not in existing_ids]
        if not to_add:
            print(f"  {file_key}: no new records")
            continue

        combined = sorted(existing + to_add, key=lambda r: r["date"], reverse=True)
        save_json_file(path, combined)
        appended[file_key] = to_add
        print(f"  {file_key}: appended {len(to_add)} new record(s)")

    return appended


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def git_commit_and_push(changed_files: list[Path]) -> None:
    if not changed_files:
        print("Git: nothing to commit.")
        return

    def run(cmd: list[str]) -> None:
        result = subprocess.run(cmd, cwd=DATA_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Git error: {result.stderr.strip()}")
            sys.exit(1)

    for f in changed_files:
        run(["git", "add", str(f)])

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    run(["git", "commit", "-m", f"sync: {timestamp}"])
    run(["git", "push"])
    print(f"  Git: committed and pushed {len(changed_files)} file(s)")


# ---------------------------------------------------------------------------
# Cloudflare Worker upload (stubbed)
# ---------------------------------------------------------------------------

def post_to_worker(appended: dict[str, list[dict]]) -> None:
    """
    POST new records to Cloudflare Worker.

    TODO: implement Worker endpoint at CF_WORKER_URL that:
      - Accepts JSON body: {"type": "strength"|"runs"|..., "records": [...]}
      - Validates CF_API_KEY from Authorization header
      - Upserts records into D1 tables
    """
    if not CF_WORKER_URL:
        print("Cloudflare: CF_WORKER_URL not set, skipping upload.")
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CF_API_KEY}",
    }

    for file_key, records in appended.items():
        payload = {"type": file_key, "records": records}
        try:
            resp = requests.post(CF_WORKER_URL, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            print(f"  Cloudflare: posted {len(records)} {file_key} record(s) -> {resp.status_code}")
        except requests.RequestException as e:
            print(f"  Cloudflare: failed to post {file_key}: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sync_start = datetime.now(timezone.utc)

    last_sync = read_last_sync()
    if last_sync is None:
        since = sync_start - timedelta(days=INITIAL_LOOKBACK_DAYS)
        print(f"No .last_sync found; fetching last {INITIAL_LOOKBACK_DAYS} days.")
    else:
        since = last_sync
        print(f"Last sync: {last_sync.isoformat()}")

    print("Connecting to Garmin...")
    client = get_garmin_client()

    print("Fetching activities since last sync...")
    activities = fetch_activities(client, since)
    print(f"  Retrieved {len(activities)} activities")

    new_by_type: dict[str, list[dict]] = {}
    for activity in activities:
        result = parse_activity(activity, client)
        if result is None:
            continue
        file_key, record = result
        new_by_type.setdefault(file_key, []).append(record)

    print("Appending to local JSON files...")
    appended = append_new_records(new_by_type)

    if appended:
        changed_files = [DATA_DIR / f"{k}.json" for k in appended]
        print("Committing and pushing to git...")
        git_commit_and_push(changed_files)

        print("Posting to Cloudflare Worker...")
        post_to_worker(appended)
    else:
        print("No new activities to sync.")

    # Write last_sync only after everything succeeded
    write_last_sync(sync_start)
    git_commit_and_push([LAST_SYNC_FILE])
    print(f"Done. Last sync updated to {sync_start.isoformat()}")


if __name__ == "__main__":
    main()
