import { useEffect, useState } from "react";
import { fetchHealth } from "./api";
import { Assistant } from "./Assistant";
import { Dashboard } from "./Dashboard";
import type { Health } from "./types";

const STORAGE_KEY = "adpilot.devOrgId";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Browser storage can be unavailable (private windows, blocked site data): never rely on it.
function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}
function writeStored(value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    /* keep working without persistence */
  }
}

function TenantPrompt({ onSave }: { onSave: (id: string) => void }) {
  const [value, setValue] = useState("");
  const valid = UUID.test(value.trim());
  return (
    <form
      className="prompt"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onSave(value.trim().toLowerCase());
      }}
    >
      <h2>Choose an organization</h2>
      <p className="muted">
        Development only: until real sign-in exists, the API identifies the organization from this id (printed by{" "}
        <code>python -m app.cli seed-mock</code>).
      </p>
      <label>
        Organization id
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" spellCheck={false} autoComplete="off" />
      </label>
      {value !== "" && !valid && <p className="error">That is not a valid id.</p>}
      <button type="submit" disabled={!valid}>
        Open dashboard
      </button>
    </form>
  );
}

function HealthStatus() {
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

export default function App() {
  const [orgId, setOrgId] = useState<string | null>(readStored);

  return (
    <div className="app">
      <header className="app-header">
        <h1>AdPilot AI</h1>
        <p className="muted">Amazon PPC analytics</p>
      </header>
      {orgId ? (
        <>
          <Dashboard
            orgId={orgId}
            onChangeOrg={() => {
              writeStored(null);
              setOrgId(null);
            }}
          />
          <Assistant orgId={orgId} />
        </>
      ) : (
        <TenantPrompt
          onSave={(id) => {
            writeStored(id);
            setOrgId(id);
          }}
        />
      )}
      <HealthStatus />
    </div>
  );
}
