export interface Health {
  status: string;
  ads_provider: string;
  data_source: "MOCK" | "AMAZON_ADS_API";
  execution_enabled: boolean;
}

// Public, non-secret config only. Never put secrets in VITE_* variables.
const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${BASE}/health`, { signal });
  if (!res.ok) throw new Error(`Health check failed (${res.status})`);
  return (await res.json()) as Health;
}
