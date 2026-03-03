import { ingestStrength, ingestCardio, isCardioType } from './sync';

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const { pathname } = new URL(request.url);

		if (pathname === '/sync' && request.method === 'POST') {
			return handleSync(request, env);
		}

		return new Response('Not Found', { status: 404 });
	},
} satisfies ExportedHandler<Env>;

async function handleSync(request: Request, env: Env): Promise<Response> {
	// Authenticate with the static API key used by garmin_sync.py
	const auth = request.headers.get('Authorization');
	if (!auth || auth !== `Bearer ${env.SYNC_API_KEY}`) {
		return new Response('Unauthorized', { status: 401 });
	}

	let body: { type?: unknown; records?: unknown };
	try {
		body = await request.json();
	} catch {
		return new Response('Bad Request: invalid JSON', { status: 400 });
	}

	const { type, records } = body;
	if (typeof type !== 'string' || !Array.isArray(records)) {
		return new Response('Bad Request: body must have string "type" and array "records"', { status: 400 });
	}

	try {
		if (type === 'strength') {
			const inserted = await ingestStrength(env.DB, records);
			return Response.json({ inserted });
		} else if (isCardioType(type)) {
			const inserted = await ingestCardio(env.DB, type, records);
			return Response.json({ inserted });
		} else {
			return new Response(`Bad Request: unknown type "${type}"`, { status: 400 });
		}
	} catch (err) {
		console.error('Sync error:', err);
		return new Response(`Internal Server Error: ${String(err)}`, { status: 500 });
	}
}
