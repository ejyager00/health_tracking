/**
 * Sync ingestion — handles POST /sync payloads from garmin_sync.py.
 *
 * Strength records are upserted into strength_sessions, then their sets are
 * deleted and re-inserted (idempotent regardless of how many times the sync
 * script runs).  Cardio records are upserted by activity_id primary key.
 */

// ---------------------------------------------------------------------------
// Types mirroring the garmin_sync.py output
// ---------------------------------------------------------------------------

interface SyncSet {
	set_num?: number; // may be absent; derived from array index if missing
	reps?: number | null;
	weight_lbs?: number | null;
	duration_s?: number | null;
}

interface Lift {
	exercise: string;
	sets: SyncSet[];
}

interface StrengthRecord {
	activity_id: number;
	date: string;
	duration_min?: number | null;
	notes?: string | null;
	lifts?: Lift[];
}

interface CardioRecord {
	activity_id: number;
	date: string;
	duration_min?: number | null;
	distance_mi?: number | null;
	avg_hr?: number | null;
	max_hr?: number | null;
	calories?: number | null;
	avg_speed_mph?: number | null;
	pace_min_per_mile?: number | null;
	elevation_gain_ft?: number | null;
	notes?: string | null;
}

// ---------------------------------------------------------------------------
// Cardio type guard
// ---------------------------------------------------------------------------

const CARDIO_TABLES = ['runs', 'walks', 'bike_rides', 'hikes'] as const;
export type CardioType = (typeof CARDIO_TABLES)[number];

export function isCardioType(type: string): type is CardioType {
	return (CARDIO_TABLES as readonly string[]).includes(type);
}

// ---------------------------------------------------------------------------
// Ingestion
// ---------------------------------------------------------------------------

export async function ingestStrength(db: D1Database, records: unknown[]): Promise<number> {
	const stmts: D1PreparedStatement[] = [];
	let count = 0;

	for (const raw of records) {
		const r = raw as StrengthRecord;
		if (!r.activity_id || !r.date) continue;

		// Upsert session row
		stmts.push(
			db
				.prepare(
					`INSERT INTO strength_sessions (activity_id, date, duration_min, notes)
					 VALUES (?, ?, ?, ?)
					 ON CONFLICT(activity_id) DO UPDATE SET
					   date         = excluded.date,
					   duration_min = excluded.duration_min,
					   notes        = excluded.notes`,
				)
				.bind(r.activity_id, r.date, r.duration_min ?? null, r.notes ?? null),
		);

		// Delete existing sets then re-insert — handles re-syncing edited sessions
		stmts.push(db.prepare(`DELETE FROM strength_sets WHERE activity_id = ?`).bind(r.activity_id));

		for (const lift of r.lifts ?? []) {
			for (let i = 0; i < lift.sets.length; i++) {
				const s = lift.sets[i];
				stmts.push(
					db
						.prepare(
							`INSERT INTO strength_sets
							   (activity_id, date, exercise, set_num, reps, weight_lbs, duration_s)
							 VALUES (?, ?, ?, ?, ?, ?, ?)`,
						)
						.bind(
							r.activity_id,
							r.date,
							lift.exercise,
							s.set_num ?? i + 1,
							s.reps ?? null,
							s.weight_lbs ?? null,
							s.duration_s ?? null,
						),
				);
			}
		}

		count++;
	}

	if (stmts.length > 0) await db.batch(stmts);
	return count;
}

export async function ingestCardio(db: D1Database, type: CardioType, records: unknown[]): Promise<number> {
	const stmts: D1PreparedStatement[] = [];
	let count = 0;

	for (const raw of records) {
		const r = raw as CardioRecord;
		if (!r.activity_id || !r.date) continue;

		// Table name is validated to be one of the four literals before this function is called.
		stmts.push(
			db
				.prepare(
					`INSERT INTO ${type}
					   (activity_id, date, duration_min, distance_mi, avg_hr, max_hr,
					    calories, avg_speed_mph, pace_min_per_mile, elevation_gain_ft, notes)
					 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
					 ON CONFLICT(activity_id) DO UPDATE SET
					   date               = excluded.date,
					   duration_min       = excluded.duration_min,
					   distance_mi        = excluded.distance_mi,
					   avg_hr             = excluded.avg_hr,
					   max_hr             = excluded.max_hr,
					   calories           = excluded.calories,
					   avg_speed_mph      = excluded.avg_speed_mph,
					   pace_min_per_mile  = excluded.pace_min_per_mile,
					   elevation_gain_ft  = excluded.elevation_gain_ft,
					   notes              = excluded.notes`,
				)
				.bind(
					r.activity_id,
					r.date,
					r.duration_min ?? null,
					r.distance_mi ?? null,
					r.avg_hr ?? null,
					r.max_hr ?? null,
					r.calories ?? null,
					r.avg_speed_mph ?? null,
					r.pace_min_per_mile ?? null,
					r.elevation_gain_ft ?? null,
					r.notes ?? null,
				),
		);

		count++;
	}

	if (stmts.length > 0) await db.batch(stmts);
	return count;
}
