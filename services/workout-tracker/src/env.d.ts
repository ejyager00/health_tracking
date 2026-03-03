// Extend the generated Cloudflare.Env with Worker secrets.
// Secrets are set via `wrangler secret put` and are not declared in wrangler.jsonc.
// This file is preserved when `wrangler types` regenerates worker-configuration.d.ts.
declare namespace Cloudflare {
	interface Env {
		SYNC_API_KEY: string;
		PASSWORD_HASH: string;
		TURNSTILE_SECRET_KEY: string;
	}
}
