import { useEffect, useState, type ReactNode } from "react";
import { fetchHealth } from "./api";
import type { Health } from "./types";

export function HealthStatus() {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error"; message: string }>({
    kind: "loading",
  });
  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((health) => setState({ kind: "ok", health }))
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: e instanceof Error ? e.message : "Unknown error" });
      });
    return () => controller.abort();
  }, []);

  return (
    <footer className="status">
      {state.kind === "loading" && <p>Checking backend…</p>}
      {state.kind === "error" && <p role="alert">Backend unavailable: {state.message}</p>}
      {state.kind === "ok" && (
        <>
          <p>Backend status: {state.health.status}</p>
          <p>
            Data source: <strong>{state.health.data_source}</strong>
            {state.health.data_source === "MOCK" && " (synthetic data, not live Amazon data)"}
          </p>
          <p>Ad execution: {state.health.execution_enabled ? "ENABLED" : "disabled"}</p>
        </>
      )}
    </footer>
  );
}

/** The page frame shared by the development and the signed-in flows. */
export function Shell({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="app">
      <header className="app-header">
        <h1>AdPilot AI</h1>
        <p className="muted">Amazon PPC analytics</p>
        {actions}
      </header>
      {children}
      <HealthStatus />
    </div>
  );
}
