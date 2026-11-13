import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setTokenGetter } from "./auth";
import { SignedIn } from "./SignedIn";
import { failWith, ORG, stubApi } from "./test-fixtures";

const auth0 = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@auth0/auth0-react", () => ({ useAuth0: () => auth0.state }));

const ORG_2 = "22222222-2222-4222-8222-222222222222";
const loginWithRedirect = vi.fn();
const logout = vi.fn();

function signedIn(extra: Record<string, unknown> = {}) {
  auth0.state = {
    isLoading: false,
    isAuthenticated: true,
    error: undefined,
    loginWithRedirect,
    logout,
    getAccessTokenSilently: vi.fn(async () => "tok-123"),
    ...extra,
  };
}

const me = (organizations: { id: string; name: string; role: string }[], email: string | null = "me@seller.test") => ({
  data: { subject: "auth0|1", email, organizations },
});

beforeEach(() => {
  window.localStorage.clear();
  loginWithRedirect.mockReset();
  logout.mockReset();
});
afterEach(() => {
  vi.unstubAllGlobals();
  setTokenGetter(null);
});

describe("SignedIn", () => {
  it("shows a progress message while the provider is working", () => {
    signedIn({ isLoading: true, isAuthenticated: false });
    stubApi();
    render(<SignedIn />);
    expect(screen.getByText("Signing you in…")).toBeInTheDocument();
  });

  it("offers sign-in when signed out, and starts the redirect", () => {
    signedIn({ isAuthenticated: false });
    stubApi();
    render(<SignedIn />);
    expect(screen.getByText(/invite-only/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(loginWithRedirect).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("Organization id")).toBeNull(); // never the development prompt
  });

  it("reports a failed sign-in", () => {
    signedIn({ isAuthenticated: false, error: new Error("access_denied") });
    stubApi();
    render(<SignedIn />);
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in failed: access_denied");
  });

  it("sends the bearer token and organization, never the development headers", async () => {
    signedIn();
    const calls = stubApi({ "/me": me([{ id: ORG, name: "Acme Sellers", role: "analyst" }]) });
    render(<SignedIn />);
    expect(await screen.findByText("23.4%")).toBeInTheDocument();
    expect(screen.getByText("Acme Sellers")).toBeInTheDocument();
    const dataCalls = calls.urls.flatMap((u, i) => (u.includes("/dashboard/summary") ? [calls.headers[i]] : []));
    expect(dataCalls[0]).toEqual({ Authorization: "Bearer tok-123", "X-Org-Id": ORG });
    for (const h of calls.headers) {
      expect(h).not.toHaveProperty("X-Dev-Org-Id");
      expect(h).not.toHaveProperty("X-Dev-Role");
    }
  });

  it("uses the real role: no role menu, and analysts do not get the Team panel", async () => {
    signedIn();
    stubApi({ "/me": me([{ id: ORG, name: "Acme", role: "analyst" }]), "/recommendations": { meta: {}, data: [] } });
    render(<SignedIn />);
    expect(await screen.findByText("Your role: analyst")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Acting as/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Team" })).toBeNull();
  });

  it("tells an uninvited user how to get access", async () => {
    signedIn();
    stubApi({ "/me": me([], "stranger@seller.test") });
    render(<SignedIn />);
    expect(await screen.findByText("No access yet")).toBeInTheDocument();
    expect(screen.getByText("stranger@seller.test")).toBeInTheDocument();
    expect(screen.queryByText("23.4%")).toBeNull();
  });

  it("lets a user with several organizations choose one, and remembers it", async () => {
    signedIn();
    stubApi({
      "/me": me([
        { id: ORG, name: "Alpha", role: "owner" },
        { id: ORG_2, name: "Beta", role: "viewer" },
      ]),
    });
    render(<SignedIn />);
    fireEvent.change(await screen.findByLabelText("Organization"), { target: { value: ORG_2 } });
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(await screen.findByText("23.4%")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(window.localStorage.getItem("adpilot.orgId")).toBe(ORG_2);
    // A viewer cannot use the assistant.
    expect(screen.queryByRole("heading", { name: /assistant/i })).toBeNull();
  });

  it("shows an error when the account cannot be loaded", async () => {
    signedIn();
    stubApi({ "/me": failWith(401, { title: "Unauthorized", detail: "Invalid or missing credentials." }) });
    render(<SignedIn />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your account");
  });

  it("signs out and returns to the app", async () => {
    signedIn();
    stubApi({ "/me": me([{ id: ORG, name: "Acme", role: "viewer" }]) });
    render(<SignedIn />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(logout).toHaveBeenCalledWith({ logoutParams: { returnTo: window.location.origin } });
  });

  it("gives admins a Team panel that invites by email, limited to their own role", async () => {
    signedIn();
    const posted: unknown[] = [];
    stubApi({
      "/me": me([{ id: ORG, name: "Acme", role: "admin" }]),
      "/members": { data: [{ id: "m1", email: "boss@seller.test", role: "owner", status: "active", created_at: "x" }] },
      "/recommendations": { meta: {}, data: [] },
    });
    const base = globalThis.fetch as unknown as (input: string, init?: RequestInit) => Promise<unknown>;
    vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
      if (init?.method === "POST" && String(input).endsWith("/members")) {
        posted.push(JSON.parse(String(init.body)));
        return { ok: true, status: 201, json: async () => ({ data: { id: "m2", email: "ana@seller.test", role: "analyst", status: "invited", created_at: "x" } }) };
      }
      return base(input, init);
    });
    render(<SignedIn />);
    expect(await screen.findByRole("heading", { name: "Team" })).toBeInTheDocument();
    expect(await screen.findByText("boss@seller.test")).toBeInTheDocument();
    const roles = Array.from((screen.getByLabelText("Role") as HTMLSelectElement).options).map((o) => o.value);
    expect(roles).toEqual(["viewer", "analyst", "admin"]); // no "owner" for an admin
    const invite = screen.getByRole("button", { name: "Invite" });
    expect(invite).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Email to invite"), { target: { value: "ana@seller.test" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "analyst" } });
    fireEvent.click(invite);
    await waitFor(() => expect(posted).toEqual([{ email: "ana@seller.test", role: "analyst" }]));
    expect(await screen.findByText(/Invited ana@seller.test as analyst/)).toBeInTheDocument();
  });
});
