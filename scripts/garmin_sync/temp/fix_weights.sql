-- Fix workout_sets weights to nearest 0.5 lb, then fully rebuild lift_stats.
-- Run with:
--   npx wrangler d1 execute workout-pwa-db --file=scripts/garmin_sync/fix_weights.sql --remote --env production

-- 1. Fix weights in the source-of-truth table.
-- UPDATE workout_sets
-- SET weight = ROUND(weight * 2) / 2
-- WHERE weight != ROUND(weight * 2) / 2;

-- 2. Rebuild lift_stats from scratch using the corrected data.
--    Mirrors the TypeScript updateLiftStats() logic in src/lib/workouts.ts:
--      recent = most recent workout session containing that lift
--      best   = session whose single best set (reps * weight) is highest
-- DELETE FROM lift_stats;

WITH
-- Every set, tagged with its workout date and lift name (lowercased)
all_sets AS (
  SELECT
    w.user_id,
    LOWER(wl.lift_name) AS lift_name,
    w.id    AS workout_id,
    w.date,
    ws.reps,
    ws.weight
  FROM workout_sets ws
  JOIN workout_lifts wl ON wl.id = ws.workout_lift_id
  JOIN workouts      w  ON w.id  = wl.workout_id
),
-- Best single-set volume (reps * weight) per (user, lift, session)
session_best AS (
  SELECT user_id, lift_name, workout_id, date,
         MAX(reps * weight) AS max_volume
  FROM all_sets
  GROUP BY user_id, lift_name, workout_id
),
-- The session with the overall best volume per (user, lift)
-- SQLite guarantees workout_id/date come from the MAX row
best_session AS (
  SELECT user_id, lift_name, workout_id, date,
         MAX(max_volume) AS best_volume
  FROM session_best
  GROUP BY user_id, lift_name
),
-- The most recent session per (user, lift)
recent_session AS (
  SELECT user_id, lift_name, workout_id, MAX(date) AS date
  FROM all_sets
  GROUP BY user_id, lift_name
)
INSERT INTO lift_stats
  (user_id, lift_name, recent_date, recent_sets_json, best_volume, best_date, best_sets_json, updated_at)
SELECT
  bs.user_id,
  bs.lift_name,
  rs.date AS recent_date,
  (
    SELECT json_group_array(json_object('reps', a.reps, 'weight', a.weight))
    FROM all_sets a
    WHERE a.user_id = rs.user_id AND a.lift_name = rs.lift_name AND a.workout_id = rs.workout_id
  ) AS recent_sets_json,
  bs.best_volume,
  bs.date AS best_date,
  (
    SELECT json_group_array(json_object('reps', a.reps, 'weight', a.weight))
    FROM all_sets a
    WHERE a.user_id = bs.user_id AND a.lift_name = bs.lift_name AND a.workout_id = bs.workout_id
  ) AS best_sets_json,
  CAST(strftime('%s', 'now') AS INTEGER) AS updated_at
FROM best_session bs
JOIN recent_session rs ON rs.user_id = bs.user_id AND rs.lift_name = bs.lift_name;
