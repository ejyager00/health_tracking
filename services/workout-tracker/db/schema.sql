-- Workout Tracker D1 Schema
-- Apply with: npx wrangler d1 execute workout-db --file=db/schema.sql

-- ---------------------------------------------------------------------------
-- Strength
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS strength_sessions (
  activity_id   INTEGER PRIMARY KEY,       -- Garmin activity ID
  date          TEXT NOT NULL,             -- "YYYY-MM-DD HH:MM:SS" local time
  duration_min  REAL,
  notes         TEXT
);

CREATE TABLE IF NOT EXISTS strength_sets (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_id   INTEGER NOT NULL REFERENCES strength_sessions(activity_id),
  date          TEXT NOT NULL,             -- denormalized from session for query convenience
  exercise      TEXT NOT NULL,
  set_num       INTEGER NOT NULL,
  reps          INTEGER,
  weight_lbs    REAL,
  duration_s    REAL                       -- for timed sets (planks, etc.), otherwise NULL
);

CREATE INDEX IF NOT EXISTS idx_sets_exercise_date
  ON strength_sets(exercise, date DESC);

-- ---------------------------------------------------------------------------
-- Cardio
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS runs (
  activity_id         INTEGER PRIMARY KEY,
  date                TEXT NOT NULL,
  duration_min        REAL,
  distance_mi         REAL,
  avg_hr              INTEGER,
  max_hr              INTEGER,
  calories            INTEGER,
  avg_speed_mph       REAL,
  pace_min_per_mile   REAL,
  elevation_gain_ft   REAL,
  notes               TEXT
);

CREATE TABLE IF NOT EXISTS walks (
  activity_id         INTEGER PRIMARY KEY,
  date                TEXT NOT NULL,
  duration_min        REAL,
  distance_mi         REAL,
  avg_hr              INTEGER,
  max_hr              INTEGER,
  calories            INTEGER,
  avg_speed_mph       REAL,
  pace_min_per_mile   REAL,
  elevation_gain_ft   REAL,
  notes               TEXT
);

CREATE TABLE IF NOT EXISTS bike_rides (
  activity_id         INTEGER PRIMARY KEY,
  date                TEXT NOT NULL,
  duration_min        REAL,
  distance_mi         REAL,
  avg_hr              INTEGER,
  max_hr              INTEGER,
  calories            INTEGER,
  avg_speed_mph       REAL,
  pace_min_per_mile   REAL,
  elevation_gain_ft   REAL,
  notes               TEXT
);

CREATE TABLE IF NOT EXISTS hikes (
  activity_id         INTEGER PRIMARY KEY,
  date                TEXT NOT NULL,
  duration_min        REAL,
  distance_mi         REAL,
  avg_hr              INTEGER,
  max_hr              INTEGER,
  calories            INTEGER,
  avg_speed_mph       REAL,
  pace_min_per_mile   REAL,
  elevation_gain_ft   REAL,
  notes               TEXT
);

-- ---------------------------------------------------------------------------
-- Routines
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS routines (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,               -- e.g. "Push A", "Leg Day", "Pull B"
  day_of_week INTEGER                      -- 0=Mon ... 6=Sun, NULL if not fixed to a day
);

CREATE TABLE IF NOT EXISTS routine_exercises (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  routine_id      INTEGER NOT NULL REFERENCES routines(id),
  exercise        TEXT NOT NULL,           -- must match normalized names in strength_sets
  sort_order      INTEGER NOT NULL,
  target_sets     INTEGER,                 -- e.g. 3
  rep_range_low   INTEGER,                 -- e.g. 8 (lower bound of target rep range)
  rep_range_high  INTEGER,                 -- e.g. 12 (upper bound; NULL = exact target)
  superset_group  INTEGER,                 -- NULL = standalone; same integer = superset partners
  notes           TEXT
);
