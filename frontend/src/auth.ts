// Sign-in configuration (ADR 0026). Public identifiers only: a domain, a client id and an API audience.
// Never put a client secret here: this is a single-page app and VITE_* values are visible to everyone.

export interface AuthConfig {
  domain: string;
  clientId: string;
  audience: string;
}

/** Sign-in is enabled only when all three values are set; otherwise the development flow is used. */
export function authConfig(): AuthConfig | null {
  const domain = import.meta.env.VITE_AUTH0_DOMAIN as string | undefined;
  const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID as string | undefined;
  const audience = import.meta.env.VITE_AUTH0_AUDIENCE as string | undefined;
  return domain && clientId && audience ? { domain, clientId, audience } : null;
}

type TokenGetter = () => Promise<string>;
let getter: TokenGetter | null = null;

/** The API client asks this for a bearer token. Null means "development headers" (no sign-in). */
export function setTokenGetter(fn: TokenGetter | null): void {
  getter = fn;
}

export function currentTokenGetter(): TokenGetter | null {
  return getter;
}
