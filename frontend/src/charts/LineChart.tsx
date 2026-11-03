import { useId, useState } from "react";
import { shortDate, niceTicks } from "../format";

export interface Series {
  key: string;
  label: string;
  /** A CSS color, normally a theme token such as "var(--series-1)". */
  color: string;
  /** One value per date; null breaks the line (undefined that day, never 0). */
  values: (number | null)[];
  format: (v: number) => string;
}

interface Props {
  title: string;
  subtitle: string;
  dates: string[];
  /** Index of the last day whose attribution window is complete; later days are drawn dashed. */
  matureThrough: number;
  series: Series[];
  axisFormat: (v: number) => string;
  reference?: { value: number; label: string };
}

const W = 720;
const H = 240;
const M = { left: 58, right: 70, top: 14, bottom: 30 };

function segments(values: (number | null)[], from: number, to: number, x: (i: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  for (let i = from; i <= to && i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  }
  return d;
}

export function LineChart({ title, subtitle, dates, matureThrough, series, axisFormat, reference }: Props) {
  const uid = useId();
  const [hover, setHover] = useState<number | null>(null);
  const n = dates.length;
  if (n === 0) return null;

  const all = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.max(...all, reference?.value ?? 0, 0));
  const top = ticks[ticks.length - 1] ?? 1;
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const x = (i: number) => M.left + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const y = (v: number) => M.top + plotH - (v / top) * plotH;
  const lastMature = Math.min(Math.max(matureThrough, 0), n - 1);

  const xTicks = Array.from(new Set([0, ...Array.from({ length: 4 }, (_, k) => Math.round(((k + 1) * (n - 1)) / 4))])).filter(
    (i) => i < n,
  );

  function indexFromPointer(e: React.PointerEvent<SVGSVGElement>): number | null {
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width === 0) return null;
    const px = ((e.clientX - rect.left) / rect.width) * W;
    return Math.min(n - 1, Math.max(0, Math.round(((px - M.left) / plotW) * (n - 1))));
  }

  function onKeyDown(e: React.KeyboardEvent<SVGSVGElement>) {
    const at = hover ?? n - 1;
    const next = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: n - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    setHover(Math.min(n - 1, Math.max(0, next)));
  }

  const summary = `${title}. ${series.map((s) => s.label).join(" and ")}, ${shortDate(dates[0] ?? "")} to ${shortDate(
    dates[n - 1] ?? "",
  )}. Use the left and right arrow keys to inspect each day.`;

  return (
    <figure className="chart" aria-labelledby={`${uid}-t`}>
      <figcaption>
        <h3 id={`${uid}-t`}>{title}</h3>
        <p className="muted">{subtitle}</p>
        {series.length > 1 && (
          <ul className="legend" aria-label="Legend">
            {series.map((s) => (
              <li key={s.key}>
                <span className="key" style={{ background: s.color }} aria-hidden="true" />
                {s.label}
              </li>
            ))}
          </ul>
        )}
      </figcaption>

      <div className="chart-frame">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={summary}
          tabIndex={0}
          onPointerMove={(e) => setHover(indexFromPointer(e))}
          onPointerLeave={() => setHover(null)}
          onFocus={() => setHover((h) => h ?? n - 1)}
          onBlur={() => setHover(null)}
          onKeyDown={onKeyDown}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} />
              <text className="tick" x={M.left - 8} y={y(t) + 4} textAnchor="end">
                {axisFormat(t)}
              </text>
            </g>
          ))}
          {xTicks.map((i) => (
            <text key={i} className="tick" x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>
              {shortDate(dates[i] ?? "")}
            </text>
          ))}

          {reference && (
            <g>
              <line className="reference" x1={M.left} x2={W - M.right} y1={y(reference.value)} y2={y(reference.value)} />
              <text className="tick" x={W - M.right + 6} y={y(reference.value) + 4}>
                {reference.label}
              </text>
            </g>
          )}

          {series.map((s) => (
            <g key={s.key} fill="none" stroke={s.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
              <path d={segments(s.values, 0, lastMature, x, y)} />
              {lastMature < n - 1 && (
                <path
                  data-testid={`maturing-${s.key}`}
                  d={segments(s.values, lastMature, n - 1, x, y)}
                  strokeDasharray="4 4"
                  opacity={0.7}
                />
              )}
            </g>
          ))}

          {series.map((s) => {
            const last = [...s.values].reverse().findIndex((v) => v !== null);
            if (last === -1) return null;
            const i = n - 1 - last;
            const v = s.values[i] as number;
            return (
              <g key={`end-${s.key}`}>
                <circle cx={x(i)} cy={y(v)} r={4} fill={s.color} className="ring" />
              </g>
            );
          })}

          {hover !== null && (
            <g pointerEvents="none">
              <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} />
              {series.map((s) => {
                const v = s.values[hover];
                return v === null || v === undefined ? null : (
                  <circle key={s.key} cx={x(hover)} cy={y(v)} r={4} fill={s.color} className="ring" />
                );
              })}
            </g>
          )}
        </svg>

        {hover !== null && (
          <div
            className="tooltip"
            role="status"
            style={{ left: `${(x(hover) / W) * 100}%`, transform: hover > n / 2 ? "translateX(calc(-100% - 12px))" : "translateX(12px)" }}
          >
            <div className="tooltip-date">
              {shortDate(dates[hover] ?? "")}
              {hover > lastMature && <span className="maturing"> · still maturing</span>}
            </div>
            {series.map((s) => (
              <div key={s.key} className="tooltip-row">
                <span className="key" style={{ background: s.color }} aria-hidden="true" />
                <strong>{s.values[hover] == null ? "—" : s.format(s.values[hover] as number)}</strong>
                <span className="muted">{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <details className="table-view">
        <summary>Table view</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Date</th>
              {series.map((s) => (
                <th key={s.key} scope="col" className="num">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dates.map((d, i) => (
              <tr key={d}>
                <th scope="row">
                  {shortDate(d)}
                  {i > lastMature ? " (maturing)" : ""}
                </th>
                {series.map((s) => (
                  <td key={s.key} className="num">
                    {s.values[i] == null ? "—" : s.format(s.values[i] as number)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
