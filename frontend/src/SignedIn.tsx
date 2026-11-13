import { useAuth0 } from "@auth0/auth0-react";
import { useState } from "react";
import { ApprovalCenter } from "./ApprovalCenter";
import { Assistant } from "./Assistant";
import { Dashboard } from "./Dashboard";
import { Insights } from "./Insights";
import { Shell } from "./Shell";
import { Team } from "./Team";
import { identity } from "./api";
import { setTokenGetter } from "./auth";
import type { OrgAccess } from "./types";
import { useResource } from "./useResource";

const STORAGE_KEY = "adpilot.orgId";

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

function OrgPicker({ orgs, onPick }: { orgs: OrgAccess[]; onPick: (id: string) => void }) {
  const [value, setValue] = useState(orgs[0]?.id ?? "");
  return (
    <form
      className="prompt"
      onSubmit={(e) => {
        e.preventDefault();
        if (value) onPick(value);
      }}
    >
      <h2>Choose an organization</h2>
      <label>
        Organization
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          {orgs.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name} ({o.role})
            </option>
          ))}
        </select>
      </label>
      <button type="submit">Open</button>
    </form>
  );
}

function Workspace() {
  const me = useResource((s) => identity.me(s), []);
  const [chosen, setChosen] = useState<string | null>(readStored);

  if (me.error) {
    return (
      <p role="alert" className="error">
        Could not load your account: {me.error.message}
      </p>
    );
  }
  if (!me.data) return <p className="muted">Loading your account…</p>;

  const orgs = me.data.organizations;
  if (orgs.length === 0) {
    return (
      <div className="prompt">
        <h2>No access yet</h2>
        <p>
          {me.data.email ? <strong>{me.data.email}</strong> : "This account"} has not been invited to an organization. Ask an owner or
          admin to invite this email address, then sign in again.
        </p>
      </div>
    );
  }
  const active = orgs.find((o) => o.id === chosen) ?? (orgs.length === 1 ? orgs[0] : undefined);
  if (!active) {
    return (
      <OrgPicker
        orgs={orgs}
        onPick={(id) => {
          writeStored(id);
          setChosen(id);
        }}
      />
    );
  }
  const analystUp = active.role !== "viewer";
  const adminUp = active.role === "admin" || active.role === "owner";
  return (
    <>
      <Dashboard
        orgId={active.id}
        orgName={active.name}
        onChangeOrg={
          orgs.length > 1
            ? () => {
                writeStored(null);
                setChosen(null);
              }
            : undefined
        }
      />
      <Insights orgId={active.id} role={active.role} />
      <ApprovalCenter orgId={active.id} fixedRole={active.role} />
      {adminUp && <Team orgId={active.id} role={active.role} />}
      {analystUp && <Assistant orgId={active.id} />}
    </>
  );
}

/** Sign-in with the identity provider (Auth0): users are invited by email, never self-registered. */
export function SignedIn() {
  const { isLoading, isAuthenticated, error, loginWithRedirect, logout, getAccessTokenSilently } = useAuth0();
  // The API client reads the token through this getter. It is set during render, before any child
  // fetches, because children's effects run before a parent's.
  setTokenGetter(
    isAuthenticated
      ? async () => {
          const token = await getAccessTokenSilently();
          if (!token) throw new Error("No access token is available.");
          return token;
        }
      : null,
  );

  if (isLoading) {
    return (
      <Shell>
        <p className="muted">Signing you in…</p>
      </Shell>
    );
  }
  if (!isAuthenticated) {
    return (
      <Shell>
        <div className="prompt">
          <h2>Sign in</h2>
          <p className="muted">AdPilot is invite-only. Sign in with the email address you were invited with.</p>
          {error && (
            <p role="alert" className="error">
              Sign-in failed: {error.message}
            </p>
          )}
          <button type="button" onClick={() => void loginWithRedirect()}>
            Sign in
          </button>
        </div>
      </Shell>
    );
  }
  return (
    <Shell
      actions={
        <button type="button" className="link" onClick={() => void logout({ logoutParams: { returnTo: window.location.origin } })}>
          Sign out
        </button>
      }
    >
      <Workspace />
    </Shell>
  );
}
