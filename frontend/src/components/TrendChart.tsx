import { useState } from "react";

import type { RunTrendPoint } from "../api/types";

const WIDTH = 640;
const HEIGHT = 200;
const PAD = { top: 12, right: 16, bottom: 30, left: 44 };

/** A point we can actually plot: pass_rate is null for runs with nothing countable. */
type Plotted = RunTrendPoint & { pass_rate: number };

export function TrendChart({ points }: { points: RunTrendPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const plotted: Plotted[] = points.filter(
    (point): point is Plotted => point.pass_rate !== null,
  );

  if (plotted.length < 2) {
    return <p className="muted">Not enough runs yet to show a trend.</p>;
  }

  const innerW = WIDTH - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;
  const x = (index: number) => PAD.left + (innerW * index) / (plotted.length - 1);
  const y = (rate: number) => PAD.top + innerH * (1 - rate);

  const line = plotted
    .map((point, index) => `${index === 0 ? "M" : "L"} ${x(index)} ${y(point.pass_rate)}`)
    .join(" ");

  const active = hover === null ? null : plotted[hover];

  return (
    <figure className="chart" data-testid="trend-chart">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Pass rate across the last ${plotted.length} runs`}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.5, 1].map((tick) => (
          <g key={tick}>
            <line
              className="chart-grid"
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(tick)}
              y2={y(tick)}
            />
            <text className="chart-tick" x={PAD.left - 8} y={y(tick) + 4} textAnchor="end">
              {Math.round(tick * 100)}%
            </text>
          </g>
        ))}

        <path className="chart-line" d={line} fill="none" />

        {plotted.map((point, index) => (
          <circle
            key={point.run_id}
            className="chart-dot"
            cx={x(index)}
            cy={y(point.pass_rate)}
            r={4}
          />
        ))}

        {/* Full-height hit strips: a 4px dot is too small to aim at. */}
        {plotted.map((point, index) => (
          <rect
            key={`hit-${point.run_id}`}
            x={x(index) - innerW / (2 * (plotted.length - 1))}
            y={PAD.top}
            width={innerW / (plotted.length - 1)}
            height={innerH}
            fill="transparent"
            onMouseEnter={() => setHover(index)}
          />
        ))}

        {active && (
          <line
            className="chart-crosshair"
            x1={x(hover as number)}
            x2={x(hover as number)}
            y1={PAD.top}
            y2={PAD.top + innerH}
          />
        )}

        <text className="chart-tick" x={PAD.left} y={HEIGHT - 8}>
          {new Date(plotted[0].started_at).toLocaleDateString()}
        </text>
        <text
          className="chart-tick"
          x={WIDTH - PAD.right}
          y={HEIGHT - 8}
          textAnchor="end"
        >
          {new Date(plotted[plotted.length - 1].started_at).toLocaleDateString()}
        </text>
      </svg>

      <figcaption className="muted" data-testid="trend-caption">
        {active
          ? `${active.name ?? active.external_id}: ${Math.round(active.pass_rate * 100)}% (${active.passed}/${active.total})`
          : `Latest: ${Math.round(plotted[plotted.length - 1].pass_rate * 100)}%`}
      </figcaption>
    </figure>
  );
}
