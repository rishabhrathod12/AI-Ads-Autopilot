import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Assistant } from "./Assistant";
import { ORG } from "./test-fixtures";
import type { AssistantRun } from "./types";

afterEach(() => vi.unstubAllGlobals());

const RUN: AssistantRun = {
  run_id: "r1",
  thread_id: "t1",
  status: "answered",
  question: "How is my ACOS?",
  answer: {
    text: "ACOS was 23.4%.",
    claims: [{ text: "ACOS was 23.4%.", evidence_ids: ["ev_1"] }],
    caveats: ["Recent days are still maturing."],
    confidence: "high",
    system_notes: ["This answer is based on MOCK (synthetic) data, not live Amazon data."],
    dropped_claims: 0,
  },
  data_used: [
    {
      evidence_id: "ev_1",
      tool: "get_dashboard_summary",
      arguments: { days: 28 },
      data_sources: ["MOCK"],
      synthetic: true,
      as_of: "2026-11-01T06:00:00+00:00",
      period: ["2026-10-04", "2026-10-31"],
      truncated: false,
      flags: [],
    },
  ],
  flags: [],
  model: "m",
  usage: { cost_usd: "0.001" },
};

interface Sent {
  url: string;
  headers: Record<string, string>;
  body: unknown;
}

function stub(reply: (url: string) => { status: number; body: unknown }) {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      sent.push({
        url,
        headers: init.headers as Record<string, string>,
        body: JSON.parse(String(init.body)),
      });
      const r = reply(url);
      return { ok: r.status < 400, status: r.status, json: async () => r.body };
    }),
  );
  return sent;
}

const ok = (url: string) =>
  url.endsWith("/threads") ? { status: 201, body: { thread_id: "t1" } } : { status: 200, body: RUN };

async function ask(question: string) {
  fireEvent.change(screen.getByLabelText("Your question"), { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("Assistant", () => {
  it("states what it can and cannot do before anything is asked", () => {
    render(<Assistant orgId={ORG} />);
    expect(screen.getByText(/cannot change any campaign, bid, budget or keyword/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("creates a thread once, sends the tenant header, and shows the cited answer with data used", async () => {
    const sent = stub(ok);
    render(<Assistant orgId={ORG} />);
    await ask("How is my ACOS?");

    expect(await screen.findByText("ACOS was 23.4%.")).toBeInTheDocument();
    expect(screen.getByText("ev_1")).toBeInTheDocument();
    const chips = screen.getByRole("list", { name: "Data used" });
    expect(within(chips).getByText("Account summary, 2026-10-04 to 2026-10-31")).toBeInTheDocument();
    expect(within(chips).getByText("MOCK")).toBeInTheDocument();
    expect(screen.getByText(/based on MOCK \(synthetic\) data/)).toBeInTheDocument();

    expect(sent.map((s) => s.url)).toEqual([
      "/api/api/v1/assistant/threads",
      "/api/api/v1/assistant/threads/t1/messages",
    ]);
    expect(sent[1]?.headers["X-Dev-Org-Id"]).toBe(ORG);
    expect(sent[1]?.body).toEqual({ question: "How is my ACOS?" });

    await ask("And last week?");
    await waitFor(() => expect(sent).toHaveLength(3));
    expect(sent[2]?.url).toBe("/api/api/v1/assistant/threads/t1/messages"); // same thread
  });

  it("explains a partially verified answer", async () => {
    stub((url) =>
      url.endsWith("/threads")
        ? { status: 201, body: { thread_id: "t1" } }
        : { status: 200, body: { ...RUN, status: "partial" } },
    );
    render(<Assistant orgId={ORG} />);
    await ask("q");
    expect(await screen.findByText(/could not be verified against your data and were removed/)).toBeInTheDocument();
  });

  it("shows a refusal or outage as words, without claims or figures", async () => {
    stub((url) =>
      url.endsWith("/threads")
        ? { status: 201, body: { thread_id: "t1" } }
        : {
            status: 200,
            body: {
              ...RUN,
              status: "llm_unavailable",
              answer: { ...RUN.answer, text: "The assistant is temporarily unavailable.", claims: [] },
              data_used: [],
            },
          },
    );
    render(<Assistant orgId={ORG} />);
    await ask("q");
    expect(await screen.findByText("The assistant is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Data used" })).not.toBeInTheDocument();
  });

  it("surfaces server errors from the problem details", async () => {
    stub((url) =>
      url.endsWith("/threads")
        ? { status: 201, body: { thread_id: "t1" } }
        : { status: 429, body: { title: "Too Many Requests", detail: "Too many questions. Wait a moment." } },
    );
    render(<Assistant orgId={ORG} />);
    await ask("q");
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many questions. Wait a moment.");
    expect(screen.getByLabelText("Your question")).not.toBeDisabled(); // can try again
  });

  it("runs a suggested question when clicked", async () => {
    const sent = stub(ok);
    render(<Assistant orgId={ORG} />);
    fireEvent.click(screen.getByRole("button", { name: "What is ACOS?" }));
    await screen.findByText("ACOS was 23.4%.");
    expect(sent[1]?.body).toEqual({ question: "What is ACOS?" });
  });
});
