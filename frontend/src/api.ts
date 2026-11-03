import type { AssistantRun, Campaigns, Envelope, Health, Keywords, Summary, Trends } from "./types";

// Public, non-secret config only. Never put secrets in VITE_* variables.
const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";
export const DEV_ORG_HEADER = "X-Dev-Org-Id";

/** A failed API call, carrying the server's problem-details title for display. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${BASE}/health`, { signal });
  if (!res.ok) throw new Error(`Health check failed (${res.status})`);
  return (await res.json()) as Health;
}

async function get<T>(path: string, orgId: string, signal?: AbortSignal): Promise<Envelope<T>> {
  // Development-only tenant header (ADR 0014): real authentication replaces this before deployment.
  const res = await fetch(`${BASE}/api/v1${path}`, { signal, headers: { [DEV_ORG_HEADER]: orgId } });
  if (!res.ok) {
    let title = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { title?: string; detail?: string };
      title = body.detail ?? body.title ?? title;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(res.status, title);
  }
  return (await res.json()) as Envelope<T>;
}

function range(from?: string, to?: string): string {
  const q = new URLSearchParams();
  if (from) q.set("from", from);
  if (to) q.set("to", to);
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function problemTitle(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { title?: string; detail?: string };
    return body.detail ?? body.title ?? `Request failed (${res.status})`;
  } catch {
    return `Request failed (${res.status})`;
  }
}

async function post<T>(path: string, orgId: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    method: "POST",
    signal,
    headers: { [DEV_ORG_HEADER]: orgId, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await problemTitle(res));
  return (await res.json()) as T;
}

export const assistant = {
  createThread: (org: string, signal?: AbortSignal) =>
    post<{ thread_id: string }>("/assistant/threads", org, { title: "Conversation" }, signal),
  ask: (org: string, threadId: string, question: string, signal?: AbortSignal) =>
    post<AssistantRun>(`/assistant/threads/${threadId}/messages`, org, { question }, signal),
};

export const analytics = {
  summary: (org: string, from?: string, to?: string, signal?: AbortSignal) =>
    get<Summary>(`/dashboard/summary${range(from, to)}`, org, signal),
  trends: (org: string, from?: string, to?: string, signal?: AbortSignal) =>
    get<Trends>(`/dashboard/trends${range(from, to)}`, org, signal),
  campaigns: (org: string, from?: string, to?: string, signal?: AbortSignal) =>
    get<Campaigns>(`/campaigns${range(from, to)}`, org, signal),
  keywords: (org: string, from?: string, to?: string, signal?: AbortSignal) =>
    get<Keywords>(`/keywords${range(from, to)}${from || to ? "&" : "?"}limit=15`, org, signal),
};
