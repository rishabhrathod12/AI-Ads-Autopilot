import { useState } from "react";
import { ApiError, members } from "./api";
import { ROLES, type Role } from "./types";
import { useResource } from "./useResource";

/** Admins and owners invite people by email. Nobody can grant a role above their own (the server enforces it too). */
export function Team({ orgId, role }: { orgId: string; role: Role }) {
  const [version, setVersion] = useState(0);
  const [email, setEmail] = useState("");
  const [grant, setGrant] = useState<Role>("viewer");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const list = useResource((s) => members.list(orgId, s), [orgId, version]);
  const grantable = ROLES.slice(0, ROLES.indexOf(role) + 1);
  const valid = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim());

  async function submit() {
    setBusy(true);
    setNotice(null);
    try {
      const added = (await members.invite(orgId, email.trim(), grant)).data;
      setNotice({ kind: "ok", text: `Invited ${added.email} as ${added.role}. They can sign in with that email.` });
      setEmail("");
      setVersion((v) => v + 1);
    } catch (e) {
      setNotice({ kind: "error", text: e instanceof ApiError ? e.message : "Could not invite that person." });
    } finally {
      setBusy(false);
    }
  }

  const rows = list.data?.data ?? [];
  return (
    <section className="team" aria-labelledby="team-title">
      <h2 id="team-title">Team</h2>
      <form
        className="invite"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid && !busy) void submit();
        }}
      >
        <label>
          Email to invite
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" spellCheck={false} />
        </label>
        <label>
          Role
          <select value={grant} onChange={(e) => setGrant(e.target.value as Role)}>
            {grantable.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={!valid || busy}>
          Invite
        </button>
      </form>
      {notice && (
        <p role={notice.kind === "error" ? "alert" : "status"} className={notice.kind === "error" ? "error" : "muted"}>
          {notice.text}
        </p>
      )}
      {list.error && (
        <p role="alert" className="error">
          {list.error.message}
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Role</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td>{m.email}</td>
              <td>{m.role}</td>
              <td>{m.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
