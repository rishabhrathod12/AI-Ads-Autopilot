import { Auth0Provider } from "@auth0/auth0-react";
import { useState } from "react";
import { ApprovalCenter } from "./ApprovalCenter";
import { Assistant } from "./Assistant";
import { Dashboard } from "./Dashboard";
import { Shell } from "./Shell";
import { SignedIn } from "./SignedIn";
import { authConfig } from "./auth";

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

/** The development flow: no sign-in, the organization id is typed. The server refuses it in production. */
function DevApp() {
  const [orgId, setOrgId] = useState<string | null>(readStored);

  return (
    <Shell>
      {orgId ? (
        <>
          <Dashboard
            orgId={orgId}
            onChangeOrg={() => {
              writeStored(null);
              setOrgId(null);
            }}
          />
          <ApprovalCenter orgId={orgId} />
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
    </Shell>
  );
}

export default function App() {
  const cfg = authConfig();
  if (!cfg) return <DevApp />;
  return (
    <Auth0Provider
      domain={cfg.domain}
      clientId={cfg.clientId}
      authorizationParams={{ redirect_uri: window.location.origin, audience: cfg.audience }}
    >
      <SignedIn />
    </Auth0Provider>
  );
}
