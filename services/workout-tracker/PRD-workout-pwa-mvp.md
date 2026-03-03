# PRD: Workout Tracker PWA (MVP)

## 1. Overview

### Purpose

A personal progressive web app (PWA) that serves as a digital workout
logbook. The primary use case is: before or during a gym session, the user
opens the app on their phone to see their scheduled lifts for the day along
with the last recorded weight, rep counts, and date for each lift. This
replaces the need to remember or manually look up previous performance.

### User

Single user (personal tool). No multi-user or social features.

### Guiding Principles

- **Read-heavy.** The app is primarily a reference tool consulted at the
  gym. Write operations (logging workouts) happen via Garmin watch + sync
  script, not through the app.
- **Lightweight and fast.** Opens quickly on mobile. No heavy frameworks
  or unnecessary dependencies.
- **Offline-tolerant.** The user may have poor gym WiFi. The app should
  degrade gracefully and cache the last-known data.
- **Minimal friction.** The most common interaction is "open app, see
  today's lifts and previous numbers." This should require zero taps beyond
  opening the app.

### MVP Scope

The MVP covers:

1. A Cloudflare Worker that ingests workout data POSTed by the local sync
   script and stores it in a D1 (SQLite) database.
2. A PWA hosted on Cloudflare Pages that displays the scheduled workout for
   the current day and looks up previous performance for each lift.
3. Password + Cloudflare Turnstile authentication protecting the PWA and
   all `/api/*` endpoints.

Manual steps that remain outside the MVP (handled by the user):

- Tagging exercises correctly in Garmin Connect after each workout.
- Running the local sync script (`garmin_sync.py`) to push data to
  Cloudflare.

## 2. Architecture & Infrastructure

All infrastructure runs on Cloudflare's free tier.

```
[Garmin Watch]
     |
     | (Garmin Connect sync)
     v
[Garmin Connect]
     |
     | (garminconnect Python lib, run manually post-workout)
     v
[garmin_sync.py — local script on user's desktop]
     |            \
     |             \ (git add/commit/push)
     |              v
     |         [GitHub repo — plain-text JSON backup]
     |
     | (HTTPS POST with API key)
     v
[Cloudflare Worker — /sync endpoint]
     |
     | (D1 SQL upserts)
     v
[Cloudflare D1 — SQLite database]
     ^
     |
     | (Worker queries, served to PWA)
     |
[Cloudflare Worker — /api/* endpoints]
     ^
     |
[PWA — Cloudflare Pages]
     ^
     |
[User's phone browser]
```

### Components

**Cloudflare Worker (`workout-tracker`)**
Handles all server-side responsibilities:
- `POST /sync` — ingestion endpoint called by the local sync script
- `GET /api/*` — read API consumed by the PWA
- `POST /login` — password + Turnstile authentication
- Serves the PWA shell (or defers to Pages — see below)

**Cloudflare D1 (`workout-db`)**
SQLite database. Single database, multiple tables. See Section 3.

**Cloudflare Pages (`workout-tracker`)**
Hosts the static PWA assets. Can share the Worker via Pages Functions
or use a separate Worker — either is acceptable.

**Local sync script (`garmin_sync.py`)**
Exists outside Cloudflare. Documented separately. Posts JSON payloads to
`POST /sync`. The agent implementing the Worker does not need to implement
or modify this script; only the Worker endpoint that receives its output.

## 3. Data Layer

### D1 Schema

#### `strength_sessions`

One row per gym session.

```sql
CREATE TABLE IF NOT EXISTS strength_sessions (
  activity_id   INTEGER PRIMARY KEY,  -- Garmin activity ID
  date          TEXT NOT NULL,         -- "YYYY-MM-DD HH:MM:SS" local time
  duration_min  REAL,
  notes         TEXT
);
```

#### `strength_sets`

One row per individual set within a session. `get_activity_exercise_sets()`
returns per-set granularity and is the authoritative source; no summarized
fallback schema is needed.

```sql
CREATE TABLE IF NOT EXISTS strength_sets (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_id   INTEGER NOT NULL REFERENCES strength_sessions(activity_id),
  date          TEXT NOT NULL,  -- denormalized from session for query convenience
  exercise      TEXT NOT NULL,
  set_num       INTEGER NOT NULL,
  reps          INTEGER,
  weight_lbs    REAL,
  duration_s    REAL            -- for timed sets (planks, etc.), otherwise NULL
);

CREATE INDEX IF NOT EXISTS idx_sets_exercise_date
  ON strength_sets(exercise, date DESC);
```

#### Cardio tables

One table per activity type. All share the same schema.

```sql
CREATE TABLE IF NOT EXISTS runs (
  activity_id       INTEGER PRIMARY KEY,
  date              TEXT NOT NULL,
  duration_min      REAL,
  distance_mi       REAL,
  avg_hr            INTEGER,
  max_hr            INTEGER,
  calories          INTEGER,
  avg_speed_mph     REAL,
  pace_min_per_mile REAL,
  elevation_gain_ft REAL,
  notes             TEXT
);

-- Repeat for: walks, bike_rides, hikes
-- (identical schema, separate tables)
```

Create `walks`, `bike_rides`, and `hikes` tables with identical schema to
`runs`.

#### `routines`

Defines the user's planned workout schedule.

```sql
CREATE TABLE IF NOT EXISTS routines (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,   -- e.g. "Push A", "Leg Day", "Pull B"
  day_of_week INTEGER          -- 0=Mon ... 6=Sun, NULL if not fixed to a day
);
```

#### `routine_exercises`

Ordered list of exercises per routine. Supersets are modeled via
`superset_group`: exercises sharing the same non-null `superset_group`
integer within a routine are performed as a superset (alternating sets).
The `sort_order` determines both the overall display order and the order
of exercises within a superset.

```sql
CREATE TABLE IF NOT EXISTS routine_exercises (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  routine_id      INTEGER NOT NULL REFERENCES routines(id),
  exercise        TEXT NOT NULL,    -- must match exercise names in strength_sets
  sort_order      INTEGER NOT NULL,
  target_sets     INTEGER,          -- e.g. 3
  rep_range_low   INTEGER,          -- e.g. 8  (lower bound of target rep range)
  rep_range_high  INTEGER,          -- e.g. 12 (upper bound; NULL = exact target)
  superset_group  INTEGER,          -- NULL = standalone; same integer = superset
  notes           TEXT
);
```

**Superset example:** Dumbbell Bench Press and Dumbbell Row paired as a
superset would both have `superset_group = 1`. A third exercise, say Lat
Pulldown, performed standalone would have `superset_group = NULL`.

The UI groups exercises with the same `superset_group` together visually
and presents their previous performance side by side.

### Exercise Name Normalization

The sync script (`garmin_sync.py`) applies a user-maintained exercise name
mapping before posting data to the Worker. Names stored in `strength_sets`
are therefore normalized and consistent — they reflect the user's canonical
exercise names, not raw Garmin strings. The `routine_exercises.exercise`
field must match these normalized names exactly. Since the user controls
both the mapping in the sync script and the routine definitions, keeping
them in sync is straightforward.

### Upsert Behavior

All sync ingestion uses upsert semantics keyed on `activity_id` to ensure
idempotency. Running the sync script multiple times must not create
duplicate records.

## 4. Sync Ingestion (Worker Endpoint)

### `POST /sync`

Called by `garmin_sync.py` after each workout. Receives a batch of new
activity records for a single activity type and upserts them into D1.

**Authentication:** `Authorization: Bearer <CF_API_KEY>` header. The key
is stored as a Cloudflare Worker secret (`wrangler secret put SYNC_API_KEY`).
Requests without a valid key return `401`.

**Request body:**

```json
{
  "type": "strength",
  "records": [ ... ]
}
```

`type` is one of: `strength`, `runs`, `walks`, `bike_rides`, `hikes`.

**Strength record shape** (as posted by sync script):

```json
{
  "activity_id": 1234567890,
  "date": "2026-02-27 07:15:00",
  "type": "strength",
  "duration_min": 62.5,
  "notes": "",
  "lifts": [
    {
      "exercise": "Barbell Back Squat",
      "sets": [
        { "set_num": 1, "reps": 8, "weight_lbs": 225.0, "duration_s": null },
        { "set_num": 2, "reps": 8, "weight_lbs": 225.0, "duration_s": null },
        { "set_num": 3, "reps": 6, "weight_lbs": 225.0, "duration_s": null }
      ]
    }
  ]
}

**Cardio record shape:**

```json
{
  "activity_id": 1234567891,
  "date": "2026-02-25 06:45:00",
  "type": "runs",
  "duration_min": 32.4,
  "distance_mi": 4.1,
  "avg_hr": 158,
  "max_hr": 174,
  "calories": 412,
  "avg_speed_mph": 7.59,
  "pace_min_per_mile": 7.91,
  "elevation_gain_ft": 112.0,
  "notes": ""
}
```

**Response:**

- `200 OK` with `{ "inserted": N }` on success
- `400 Bad Request` if body is malformed or `type` is unrecognized
- `401 Unauthorized` if API key is missing or invalid
- `500 Internal Server Error` on D1 failure (with error message in body)

## 5. PWA Features & UI

### API Endpoints (Worker)

The Worker exposes the following read endpoints consumed by the PWA. These
do not require authentication (the app is personal; security is via
obscurity of the URL + the data is not sensitive). If the owner wants auth
added later, a simple cookie or header token can be added.

---

**`GET /api/routines`**

Returns all routines with their exercises in order.

```json
[
  {
    "id": 1,
    "name": "Leg Day",
    "day_of_week": 0,
    "exercises": [
      {
        "id": 1,
        "exercise": "Barbell Back Squat",
        "sort_order": 1,
        "target_sets": 3,
        "rep_range_low": 8,
        "rep_range_high": 12,
        "superset_group": null,
        "notes": null
      },
      {
        "id": 2,
        "exercise": "Dumbbell Bench Press",
        "sort_order": 2,
        "target_sets": 3,
        "rep_range_low": 10,
        "rep_range_high": 15,
        "superset_group": 1,
        "notes": null
      },
      {
        "id": 3,
        "exercise": "Dumbbell Row",
        "sort_order": 3,
        "target_sets": 3,
        "rep_range_low": 10,
        "rep_range_high": 15,
        "superset_group": 1,
        "notes": null
      }
    ]
  }
]
```

---

**`GET /api/routine/:id/last-lifts`**

For a given routine, returns the most recent set data for each exercise in
the routine. The query scans `strength_sets` in reverse chronological order
and returns the first match per exercise name.

```json
[
  {
    "exercise": "Barbell Back Squat",
    "last_date": "2026-02-24 07:30:00",
    "sets": [
      { "set_num": 1, "reps": 8, "weight_lbs": 225.0 },
      { "set_num": 2, "reps": 8, "weight_lbs": 225.0 },
      { "set_num": 3, "reps": 6, "weight_lbs": 225.0 }
    ]
  }
]
```

If no previous data exists for an exercise, the entry is still returned
with `last_date: null` and `sets: []` so the UI can render a placeholder.

---

**`GET /api/exercise/:name/history?limit=10`**

Returns the last N sessions in which a given exercise appears, with full
set detail. Used for a drill-down history view.

```json
[
  {
    "date": "2026-02-24 07:30:00",
    "activity_id": 1234567890,
    "sets": [ ... ]
  }
]
```

`name` should be URL-encoded. `limit` defaults to 10.

---

**`GET /api/routines` (CRUD — optional for MVP)**
**`POST /api/routines`**
**`PUT /api/routines/:id`**
**`DELETE /api/routines/:id`**

Simple CRUD for managing routines and their exercises. The user will need
to seed their routine data either via these endpoints, direct D1 SQL
(Wrangler CLI), or a simple admin UI. For MVP, a seed SQL script is
acceptable instead of full CRUD endpoints.

---

### PWA Views

#### Home / Today's Workout

The default view on open (after authentication). Shows:

- The name of today's scheduled routine (matched by `day_of_week`, or a
  manual selector if today has no assigned routine or the user wants to
  override).
- The exercise list in `sort_order`. Standalone exercises render as
  individual rows. Exercises sharing a `superset_group` are visually
  grouped together with a label indicating they are a superset.
- For each exercise: target sets and rep range (e.g. `3 x 8-12`), the
  previous weight and per-set rep scheme (e.g. `225 lbs — 8/8/6`), and
  the date last performed (e.g. `Feb 24`).
- If no previous data exists for an exercise: `No history` placeholder.

The previous data is fetched from `GET /api/routine/:id/last-lifts` on
page load.

#### Exercise History Drill-Down

Tapping an exercise opens a history view showing the last ~10 sessions for
that exercise (from `GET /api/exercise/:name/history`). Displayed as a
simple reverse-chronological list: date, weight, reps per set.

#### Routine Selector / Override

A simple UI to switch which routine is displayed (for days the user deviates
from the schedule or wants to preview a different day's workout).

### PWA Requirements

- Installable on iOS and Android (manifest + service worker).
- Service worker caches the last-fetched routine and lift data for offline
  viewing. Stale cache is acceptable when offline; the app should indicate
  when data is from cache.
- Responsive, mobile-first layout. The primary device is a phone held in
  portrait orientation at the gym.
- Password + Turnstile login gate. See Section 6 for details. After a
  successful login the app opens directly to today's workout with no
  further friction.
- No workout logging UI in MVP. The app is read-only from the user's
  perspective. Workouts are logged via Garmin watch.

## 6. Authentication & Security

### PWA Login (`POST /login`)

The app is protected by a password + Cloudflare Turnstile gate. All routes
except `/login` and `/assets/*` require a valid session token.

**Flow:**

1. Unauthenticated user visits any app URL and is redirected to `/login`.
2. Login page renders a password field and a Cloudflare Turnstile widget.
3. On submit, the browser POSTs `{ password, turnstileToken }` to
   `POST /login`.
4. The Worker:
   a. Verifies the Turnstile token against Cloudflare's siteverify API
      using `TURNSTILE_SECRET_KEY` (Worker secret).
   b. Compares `bcrypt(password)` against `PASSWORD_HASH` (Worker secret).
   c. On success, generates a cryptographically random session token,
      stores it in KV with a TTL of 30 days, and sets it as an
      `HttpOnly; Secure; SameSite=Strict` cookie named `session`.
   d. Redirects to `/`.
5. Subsequent requests carry the cookie. The Worker validates it against
   KV on every request to `/` or `/api/*`.
6. Invalid/expired cookie redirects to `/login`.

**Token persistence:** 30 days. This is a single-user personal tool opened
frequently at the gym; a 30-day TTL means the user re-authenticates roughly
once a month, which is acceptable friction. The session is renewed (TTL
reset) on each authenticated request to minimize re-auth frequency during
active use periods.

**Cloudflare secrets required:**

| Secret | Description |
|---|---|
| `PASSWORD_HASH` | bcrypt hash of the login password |
| `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile secret key |
| `SYNC_API_KEY` | Bearer token for `POST /sync` |

**Cloudflare KV namespace required:** `SESSIONS` — stores
`sessionToken -> expiry` mappings.

**Turnstile site key** is a public value embedded in the login page HTML
(not a secret). It should be stored in a `wrangler.toml` variable or
hardcoded in the frontend.

### Sync endpoint (`POST /sync`)

Protected by a separate static API key (`SYNC_API_KEY`) sent as
`Authorization: Bearer <key>` by the sync script. This endpoint is
independent of the session cookie system — it is a machine-to-machine call
and does not go through the login flow.

### CORS

The Worker sets CORS headers permitting requests from the Cloudflare Pages
domain. During development, `localhost` origins are also permitted.

## 7. Out of Scope (MVP)

The following are explicitly deferred to post-MVP:

- **In-app workout logging.** Workouts are logged via Garmin watch only.
  The app does not write workout data.
- **Automatic Garmin tagging.** Exercise identification correction is done
  manually in Garmin Connect before running the sync script.
- **Automated sync triggering.** The sync script is run manually by the
  user post-workout. No Lambda, scheduling, or push-button sync from the
  PWA.
- **Progressive overload suggestions.** The app displays previous
  performance; it does not recommend target weights or reps.
- **Charts and trend visualizations.** History drill-down is a plain list
  only.
- **Cardio display in PWA.** The sync script ingests cardio data and stores
  it in D1, but the PWA does not display it in MVP. The cardio tables and
  sync endpoint should still be implemented so the data is available for
  future use.
- **Routine editing UI.** Routines are seeded via SQL or a seed script.
  Full CRUD UI is deferred.
- **User accounts or sharing.**

## 8. Open Questions & Known Unknowns

### Routine seed data

The implementing agent should create a seed SQL script
(`seed_routines.sql`) with placeholder routine structure that the user can
edit. The user's actual routine structure (exercise names, rep ranges, set
counts, superset groupings) will be provided separately or edited directly
in the seed script before the first deployment.

Exercise names in `seed_routines.sql` must exactly match the normalized
names produced by the sync script's exercise name mapping. The user is
responsible for keeping these consistent.