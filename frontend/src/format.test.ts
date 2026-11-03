import { describe, expect, it } from "vitest";
import { count, DASH, isoDaysBefore, longDate, multiple, niceTicks, percent, rupees, rupeesCompact, rupeesExact, shortDate } from "./format";

describe("formatting", () => {
  it("shows rupees from integer paise with Indian digit grouping", () => {
    expect(rupees(1234567800)).toBe("₹1,23,45,678");
    expect(rupees(0)).toBe("₹0");
    expect(rupeesExact(1300)).toBe("₹13.00");
    expect(rupeesCompact(5_000_000)).toMatch(/^₹/);
  });

  it("renders every undefined value as a dash, never as zero", () => {
    expect(rupees(null)).toBe(DASH);
    expect(rupeesExact(null)).toBe(DASH);
    expect(percent(null)).toBe(DASH);
    expect(multiple(null)).toBe(DASH);
    expect(count(null)).toBe(DASH);
  });

  it("keeps a genuine zero as zero", () => {
    expect(percent(0)).toBe("0.0%");
    expect(count(0)).toBe("0");
  });

  it("formats percentages, multiples and dates", () => {
    expect(percent(12.345)).toBe("12.3%");
    expect(percent(0.5, 2)).toBe("0.50%");
    expect(multiple(5)).toBe("5.00×");
    expect(shortDate("2026-10-31")).toBe("31 Oct");
    expect(longDate("2026-10-31")).toBe("31 Oct 2026");
  });

  it("does ISO date arithmetic without timezone drift", () => {
    expect(isoDaysBefore("2026-03-01", 1)).toBe("2026-02-28");
    expect(isoDaysBefore("2026-10-31", 6)).toBe("2026-10-25");
    expect(isoDaysBefore("2026-01-01", 1)).toBe("2025-12-31");
  });
});

describe("niceTicks", () => {
  it("returns clean, increasing ticks that cover the maximum", () => {
    for (const max of [1, 7, 95, 9500, 123456, 3_900_000]) {
      const t = niceTicks(max);
      expect(t[0]).toBe(0);
      expect(t[t.length - 1]).toBeGreaterThanOrEqual(max);
      expect(t.length).toBeLessThanOrEqual(8);
      expect(t).toEqual([...t].sort((a, b) => a - b));
    }
    expect(niceTicks(9500)).toEqual([0, 2500, 5000, 7500, 10000]);
  });

  it("handles empty or degenerate input", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(Number.NaN)).toEqual([0, 1]);
  });
});
