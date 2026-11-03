// Presentation helpers. An undefined value (null) always renders as an em dash, never as 0.

import type { Num } from "./types";

export const DASH = "—";

const rupee = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const rupeeCents = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

/** Integer paise -> whole rupees, Indian digit grouping. */
export function rupees(paise: Num): string {
  return paise === null ? DASH : rupee.format(paise / 100);
}

/** Paise -> rupees and paise (for per-click values). */
export function rupeesExact(paise: Num): string {
  return paise === null ? DASH : rupeeCents.format(paise / 100);
}

/** Axis tick: compact rupees, e.g. ₹12K. */
export function rupeesCompact(paise: number): string {
  return `₹${compact.format(paise / 100)}`;
}

export function count(value: Num): string {
  return value === null ? DASH : plain.format(value);
}

export function percent(value: Num, digits = 1): string {
  return value === null ? DASH : `${value.toFixed(digits)}%`;
}

export function multiple(value: Num): string {
  return value === null ? DASH : `${value.toFixed(2)}×`;
}

/** "2026-10-31" -> "31 Oct". Parsed as a calendar date, so no timezone shift. */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1)).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function isoDaysBefore(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1));
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

/** Round up to a clean axis maximum with evenly spaced ticks: [0, 1000, 2000, ...]. */
export function niceTicks(max: number, target = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const rough = max / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = ([1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? 10 * magnitude);
  const ticks: number[] = [];
  for (let v = 0; v < max + step; v += step) {
    ticks.push(Number(v.toPrecision(12)));
    if (v >= max) break;
  }
  return ticks;
}
