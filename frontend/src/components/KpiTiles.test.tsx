import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SUMMARY } from "../test-fixtures";
import { describeDelta, KpiTiles } from "./KpiTiles";

const change = (absolute: number | null, relative: number | null) => ({ current: 1, previous: 1, absolute, relative_pct: relative });

describe("describeDelta", () => {
  it("marks a rising ACOS as worse and a rising sales figure as better", () => {
    expect(describeDelta(change(2.5, 12), "points", "down")).toEqual({ text: "+2.5 pts", tone: "bad", arrow: "▲" });
    expect(describeDelta(change(0, 10), "relative", "up")).toEqual({ text: "+10.0%", tone: "good", arrow: "▲" });
    expect(describeDelta(change(-1.5, 0), "points", "down")).toEqual({ text: "−1.5 pts", tone: "good", arrow: "▼" });
  });

  it("treats spend as neutral, since more spend is neither good nor bad", () => {
    expect(describeDelta(change(0, 12.5), "relative", null)?.tone).toBe("neutral");
  });

  it("shows no direction for a change that rounds to zero", () => {
    expect(describeDelta(change(0.02, 0.01), "points", "down")).toEqual({ text: "0.0 pts", tone: "neutral", arrow: "■" });
  });

  it("returns nothing when the change is undefined", () => {
    expect(describeDelta(undefined, "points", "down")).toBeNull();
    expect(describeDelta(change(null, null), "points", "down")).toBeNull();
    expect(describeDelta(change(null, 5), "points", "down")).toBeNull(); // wrong style for this metric
  });
});

describe("KpiTiles", () => {
  it("renders values and signed deltas with an arrow and a text label, not colour alone", () => {
    render(<KpiTiles summary={SUMMARY} comparison={SUMMARY.comparison} />);
    const acos = screen.getByText("ACOS").closest(".tile") as HTMLElement;
    expect(within(acos).getByText("23.4%")).toBeInTheDocument();
    expect(within(acos).getByText(/\+2\.5 pts/)).toBeInTheDocument();
    expect(within(acos).getByText("(worse)")).toBeInTheDocument();
    expect(within(acos).getByText("▲")).toHaveAttribute("aria-hidden", "true");
    const sales = screen.getByText("Ad sales").closest(".tile") as HTMLElement;
    expect(within(sales).getByText(/−4\.2%/)).toBeInTheDocument();
    expect(within(sales).getByText("(worse)")).toBeInTheDocument();
  });

  it("shows a dash for an undefined KPI and no delta without a comparison", () => {
    const summary = { ...SUMMARY, kpis: { ...SUMMARY.kpis, acos_pct: null, roas: null } };
    render(<KpiTiles summary={summary} comparison={null} />);
    const acos = screen.getByText("ACOS").closest(".tile") as HTMLElement;
    expect(within(acos).getAllByText("—")).toHaveLength(2); // the value and the missing delta
    expect(within(acos).queryByText(/pts/)).toBeNull();
  });

  it("explains that ad sales are not total revenue", () => {
    render(<KpiTiles summary={SUMMARY} comparison={null} />);
    expect(screen.getByText("Ad sales").closest(".tile")).toHaveAttribute("title", expect.stringContaining("not total business revenue"));
  });
});
