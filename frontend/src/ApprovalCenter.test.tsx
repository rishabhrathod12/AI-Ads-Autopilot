import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApprovalCenter } from "./ApprovalCenter";
import { ORG } from "./test-fixtures";
import type { Recommendation } from "./types";

afterEach(() => vi.unstubAllGlobals());

const HASH = "a".repeat(64);
const FUTURE = "2099-01-01T00:00:00+00:00";
const PAST = "2000-01-01T00:00:00+00:00";

function rec(over: Partial<Recommendation> = {}): Recommendation {
  return {
    id: "r1",
    type: "bid_change",
    status: "pending",
    entity: { type: "target", id: "t1", label: "yoga mat" },
    current_value_paise: 1000,
    proposed_value_paise: 900,
    proposed_text: null,
    rationale: "ACOS 41.0% is above the 30% target.",
    evidence: { rule: "bid_scale_to_target_acos", clicks: 120, orders: 6 },
    confidence: "medium",
    expected_impact: "Moves this target's ACOS toward target. No result is predicted.",
    data_source: "MOCK",
    synthetic: true,
    content_hash: HASH,
    created_at: "2026-11-01T00:00:00+00:00",
    expires_at: FUTURE,
    approval: null,
    ...over,
  };
}

interface Call {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

/** Serves `items` for GET list; `onPost` answers POSTs. Records every call. */
function stub(items: () => Recommendation[], onPost?: (call: Call) => { status: number; body: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const call: Call = {
        method: init.method ?? "GET",
        url,
        headers: (init.headers ?? {}) as Record<string, string>,
        body: init.body ? JSON.parse(String(init.body)) : null,
      };
      calls.push(call);
      const reply =
        call.method === "GET"
          ? { status: 200, body: { data: items(), meta: { synthetic: true, notice: "MOCK data: synthetic, not live Amazon data." } } }
          : (onPost?.(call) ?? { status: 200, body: { data: {} } });
      return { ok: reply.status < 400, status: reply.status, json: async () => reply.body };
    }),
  );
  return calls;
}

async function open(role: string) {
  render(<ApprovalCenter orgId={ORG} />);
  fireEvent.change(screen.getByLabelText(/acting as/i), { target: { value: role } });
  await screen.findByText("yoga mat");
}

describe("ApprovalCenter", () => {
  it("shows the proposal with its change, rationale, evidence and MOCK labelling", async () => {
    stub(() => [rec()]);
    render(<ApprovalCenter orgId={ORG} />);
    expect(await screen.findByText("yoga mat")).toBeInTheDocument();
    expect(screen.getByText("₹10 → ₹9")).toBeInTheDocument();
    expect(screen.getByText(/ACOS 41.0% is above/)).toBeInTheDocument();
    expect(screen.getByText("MOCK")).toBeInTheDocument();
    expect(screen.getByText(/nothing is sent to Amazon and ad execution is disabled/i)).toBeInTheDocument();
    expect(screen.getByText(HASH.slice(0, 12))).toBeInTheDocument();
    fireEvent.click(screen.getByText(/evidence behind this proposal/i));
    expect(screen.getByText("bid_scale_to_target_acos")).toBeInTheDocument(); // the exact rule that produced it
    expect(screen.getByText("120")).toBeInTheDocument(); // the clicks it was computed from
  });

  it("renders a negative keyword proposal by its term, not a number", async () => {
    stub(() => [rec({ type: "negative_keyword", current_value_paise: null, proposed_value_paise: null, proposed_text: "free pdf" })]);
    render(<ApprovalCenter orgId={ORG} />);
    expect(await screen.findByText(/Add “free pdf” as a negative keyword/)).toBeInTheDocument();
  });

  it("sends the development role header on every call", async () => {
    const calls = stub(() => [rec()]);
    await open("admin");
    await waitFor(() => expect(calls.some((c) => c.headers["X-Dev-Role"] === "admin")).toBe(true));
    expect(calls.every((c) => c.headers["X-Dev-Org-Id"] === ORG)).toBe(true);
  });

  it("gives viewers no actions, analysts only 'Generate', and admins the decision buttons", async () => {
    stub(() => [rec()]);
    await open("viewer");
    expect(screen.queryByRole("button", { name: /generate proposals/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^approve \w/i })).toBeNull();

    fireEvent.change(screen.getByLabelText(/acting as/i), { target: { value: "analyst" } });
    expect(await screen.findByRole("button", { name: /generate proposals/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^approve \w/i })).toBeNull();

    fireEvent.change(screen.getByLabelText(/acting as/i), { target: { value: "admin" } });
    expect(await screen.findByRole("button", { name: "Approve yoga mat" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject yoga mat" })).toBeInTheDocument();
  });

  it("approval needs a confirmation, says nothing is executed, and is bound to the reviewed hash", async () => {
    let items = [rec()];
    const calls = stub(
      () => items,
      () => {
        items = [rec({ status: "approved", approval: { id: "a1", decision: "approve", decided_by: "dev", decided_at: "2026-11-02T00:00:00+00:00", comment: "ok", content_hash: HASH } })];
        return { status: 200, body: { data: items[0] } };
      },
    );
    await open("admin");
    fireEvent.click(screen.getByRole("button", { name: "Approve yoga mat" }));
    const confirm = screen.getByRole("group", { name: /confirm approve/i });
    expect(within(confirm).getByText(/nothing is executed/i)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0); // no request before confirming
    fireEvent.change(within(confirm).getByLabelText(/comment/i), { target: { value: "  ok  " } });
    fireEvent.click(within(confirm).getByRole("button", { name: /confirm approve/i }));

    expect(await screen.findByText(/Approved\. Nothing was executed\./)).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toContain("/recommendations/r1/approve");
    expect(post.body).toEqual({ content_hash: HASH, comment: "ok" });
    expect(post.headers["X-Dev-Role"]).toBe("admin");
    // the list is reloaded: the filter is "pending", so the approved item is no longer asked for
    await waitFor(() => expect(calls.filter((c) => c.method === "GET").length).toBeGreaterThan(1));
  });

  it("can be cancelled without sending anything, and rejection uses the reject endpoint", async () => {
    const calls = stub(() => [rec()]);
    await open("owner");
    fireEvent.click(screen.getByRole("button", { name: "Approve yoga mat" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Approve yoga mat" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reject yoga mat" }));
    fireEvent.click(screen.getByRole("button", { name: /confirm reject/i }));
    await screen.findByText(/Rejected\. Nothing was executed\./);
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toContain("/recommendations/r1/reject");
    expect(post.body).toEqual({ content_hash: HASH, comment: null });
  });

  it("shows the server's reason when the content changed or it already expired, and changes nothing locally", async () => {
    stub(
      () => [rec()],
      () => ({ status: 409, body: { title: "Content hash does not match", detail: "the approved content hash does not match the stored content" } }),
    );
    await open("admin");
    fireEvent.click(screen.getByRole("button", { name: "Approve yoga mat" }));
    fireEvent.click(screen.getByRole("button", { name: /confirm approve/i }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/does not match the stored content/);
    expect(alert).toHaveTextContent(/reload the list and review the current version/i);
  });

  it("explains a 403 and does not offer decisions on expired or already-decided items", async () => {
    stub(
      () => [rec({ id: "old", entity: { type: "target", id: "t9", label: "old keyword" }, expires_at: PAST }), rec({ id: "done", status: "rejected", entity: { type: "target", id: "t8", label: "done keyword" } })],
      () => ({ status: 403, body: { title: "Requires role admin" } }),
    );
    render(<ApprovalCenter orgId={ORG} />);
    fireEvent.change(screen.getByLabelText(/acting as/i), { target: { value: "admin" } });
    await screen.findByText("old keyword");
    expect(screen.getAllByText("expired").length).toBeGreaterThan(0); // pending but past its expiry
    expect(screen.queryByRole("button", { name: /^approve \w/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reject old keyword|reject done keyword/i })).toBeNull();
  });

  it("generates proposals for an analyst and reports how many were new", async () => {
    const calls = stub(
      () => [],
      () => ({ status: 201, body: { data: { proposed: 5, created: 3, already_stored: 2, dropped_by_guardrails: 0 } } }),
    );
    render(<ApprovalCenter orgId={ORG} />);
    fireEvent.click(await screen.findByRole("button", { name: /generate proposals/i }));
    expect(await screen.findByText("3 new proposal(s); 2 already stored.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.url).toContain("/recommendations/generate");
  });

  it("filters by status through the query string and shows an empty state", async () => {
    const calls = stub(() => []);
    render(<ApprovalCenter orgId={ORG} />);
    expect(await screen.findByText(/No pending recommendations/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "approved" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=approved"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "all" }));
    await waitFor(() => expect(calls[calls.length - 1]!.url.endsWith("/recommendations")).toBe(true));
  });

  it("shows who decided and states that nothing was sent to Amazon", async () => {
    stub(() => [rec({ status: "approved", approval: { id: "a1", decision: "approve", decided_by: "admin-7", decided_at: "2026-11-02T10:00:00+00:00", comment: "looks right", content_hash: HASH } })]);
    render(<ApprovalCenter orgId={ORG} />);
    fireEvent.click(await screen.findByRole("button", { name: "approved" }));
    expect(await screen.findByText(/Approved by admin-7/)).toBeInTheDocument();
    expect(screen.getByText(/“looks right”/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing was sent to Amazon/)).toBeInTheDocument();
  });

  it("reports a failed load", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503, json: async () => ({ title: "Database not configured." }) })));
    render(<ApprovalCenter orgId={ORG} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/Database not configured/);
  });
});
