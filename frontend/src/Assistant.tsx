import { useRef, useState } from "react";
import { assistant } from "./api";
import type { AssistantRun, DataUsed, RunStatus } from "./types";

const TOOL_LABELS: Record<string, string> = {
  get_dashboard_summary: "Account summary",
  get_campaign_overview: "Campaign overview",
  get_campaign_changes: "Campaign changes",
  get_keyword_findings: "Keyword findings",
  get_top_keywords: "Top keywords",
  get_anomalies: "Anomalies",
  get_account_forecast: "Stored forecast",
  search_knowledge: "Reference guide",
};

const STATUS_NOTES: Partial<Record<RunStatus, string>> = {
  partial: "Some statements could not be verified against your data and were removed.",
  unverified: "Nothing could be verified against your data, so no figures are shown.",
};

const SUGGESTIONS = [
  "How is my account doing this month?",
  "Which campaigns need attention?",
  "Why did my ACOS change?",
  "What is ACOS?",
];

function chipLabel(d: DataUsed): string {
  const base = TOOL_LABELS[d.tool] ?? d.tool;
  return d.period ? `${base}, ${d.period[0]} to ${d.period[1]}` : base;
}

function DataChips({ items }: { items: DataUsed[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="chips" aria-label="Data used">
      {items.map((d) => (
        <li key={d.evidence_id} className="chip" title={d.as_of ? `As of ${d.as_of}` : undefined}>
          <span>{chipLabel(d)}</span>
          {d.synthetic && <strong className="chip-tag">MOCK</strong>}
          {d.truncated && <span className="chip-tag">trimmed</span>}
        </li>
      ))}
    </ul>
  );
}

function Answer({ run }: { run: AssistantRun }) {
  const note = STATUS_NOTES[run.status];
  const failed = !(run.status === "answered" || run.status === "partial");
  return (
    <div className={failed ? "answer failed" : "answer"}>
      {failed || run.answer.claims.length === 0 ? (
        <p>{run.answer.text}</p>
      ) : (
        <>
          <ul className="claims">
            {run.answer.claims.map((c, i) => (
              <li key={i}>
                {c.text}{" "}
                {c.evidence_ids.map((id) => (
                  <sup key={id} className="cite">
                    {id}
                  </sup>
                ))}
              </li>
            ))}
          </ul>
          {run.answer.caveats.map((c, i) => (
            <p key={i} className="muted">
              Note: {c}
            </p>
          ))}
        </>
      )}
      {note && (
        <p className="notice" role="note">
          {note}
        </p>
      )}
      {!failed &&
        run.answer.system_notes.map((n, i) => (
          <p key={i} className="muted">
            Note: {n}
          </p>
        ))}
      {run.flags.includes("possible_prompt_injection") && (
        <p className="notice" role="note">
          Some text in your data looked like instructions. It was treated as data and ignored.
        </p>
      )}
      <DataChips items={run.data_used} />
    </div>
  );
}

interface Turn {
  question: string;
  run: AssistantRun | null;
  error: string | null;
}

export function Assistant({ orgId }: { orgId: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const thread = useRef<string | null>(null);

  async function submit(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setText("");
    setTurns((t) => [...t, { question: q, run: null, error: null }]);
    let outcome: Pick<Turn, "run" | "error">;
    try {
      thread.current ??= (await assistant.createThread(orgId)).thread_id;
      outcome = { run: await assistant.ask(orgId, thread.current, q), error: null };
    } catch (e) {
      outcome = { run: null, error: e instanceof Error ? e.message : "Something went wrong." };
    }
    setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, ...outcome } : turn)));
    setBusy(false);
  }

  return (
    <section className="assistant" aria-labelledby="assistant-title">
      <h2 id="assistant-title">Ask about your ads</h2>
      <p className="muted">
        Answers use only your tracked data and are checked against it before you see them. The assistant
        cannot change any campaign, bid, budget or keyword.
      </p>
      {turns.length === 0 && (
        <ul className="suggestions" aria-label="Suggested questions">
          {SUGGESTIONS.map((s) => (
            <li key={s}>
              <button type="button" className="link" disabled={busy} onClick={() => void submit(s)}>
                {s}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="turns" aria-live="polite">
        {turns.map((t, i) => (
          <article key={i} className="turn">
            <p className="question">{t.question}</p>
            {t.run && <Answer run={t.run} />}
            {t.error && (
              <p role="alert" className="error">
                {t.error}
              </p>
            )}
            {!t.run && !t.error && <p className="muted">Checking your data…</p>}
          </article>
        ))}
      </div>
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(text);
        }}
      >
        <label className="visually-hidden" htmlFor="question">
          Your question
        </label>
        <input
          id="question"
          value={text}
          maxLength={2000}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question about your ad performance"
          autoComplete="off"
        />
        <button type="submit" disabled={busy || text.trim() === ""}>
          Ask
        </button>
      </form>
    </section>
  );
}
