import { useEffect, useState } from "react";
import { fetchHealth, type Health } from "./api";

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

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
    <main style={{ fontFamily: "system-ui", maxWidth: 640, margin: "3rem auto", padding: "0 1rem" }}>
      <h1>AdPilot AI</h1>
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
    </main>
  );
}
