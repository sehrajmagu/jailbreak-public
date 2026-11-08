"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, tdClass, thClass } from "@/components/ui";
import { cn, formatDay, formatNumber } from "@/lib/format";
import type { OverviewPoint } from "@/lib/types";

const CHART_HEIGHT = 260;
const axisProps = {
  tick: { fill: "var(--muted)", fontSize: 12 },
  tickLine: false,
  axisLine: { stroke: "var(--border-strong)" },
};
const tooltipProps = {
  labelFormatter: (label: unknown) => formatDay(String(label)),
  contentStyle: {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    fontSize: 13,
  },
  labelStyle: { color: "var(--foreground)", fontWeight: 500 },
  itemStyle: { color: "var(--foreground)" },
};

function EndLabel({
  x,
  y,
  index,
  value,
  last,
  name,
}: {
  x?: number | string;
  y?: number | string;
  index?: number;
  value?: unknown;
  last: number;
  name: string;
}) {
  if (index !== last) return null;
  return (
    <text x={Number(x) + 8} y={Number(y) + 4} fontSize={12} fill="var(--foreground)">
      {name} {String(value)}
    </text>
  );
}

function ChartTable({ series }: { series: OverviewPoint[] }) {
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-sm text-muted">View daily figures as a table</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead>
            <tr>
              <th scope="col" className={thClass}>Day</th>
              <th scope="col" className={cn(thClass, "text-right")}>Attempts</th>
              <th scope="col" className={cn(thClass, "text-right")}>Solves</th>
              <th scope="col" className={cn(thClass, "text-right")}>Tokens consumed</th>
            </tr>
          </thead>
          <tbody>
            {series.map((point) => (
              <tr key={point.date}>
                <td className={tdClass}>{formatDay(point.date)}</td>
                <td className={cn(tdClass, "text-right")}>{point.attempts}</td>
                <td className={cn(tdClass, "text-right")}>{point.solves}</td>
                <td className={cn(tdClass, "text-right")}>{formatNumber(point.tokensConsumed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

export function OverviewCharts({ series }: { series: OverviewPoint[] }) {
  const last = series.length - 1;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="min-w-0">
        <h2 className="font-medium">Attempts and solves per day</h2>
        <div className="mt-1 mb-3 flex gap-4 text-xs text-muted">
          <LegendItem color="var(--series-1)" label="Attempts" />
          <LegendItem color="var(--series-2)" label="Solves" />
        </div>
        <div style={{ height: CHART_HEIGHT }} role="img" aria-label="Line chart of attempts and solves per day. The same figures are in the table below.">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 8, right: 84, bottom: 0, left: -16 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="date" tickFormatter={formatDay} {...axisProps} />
              <YAxis allowDecimals={false} {...axisProps} axisLine={false} />
              <Tooltip {...tooltipProps} />
              <Line
                dataKey="attempts"
                name="Attempts"
                stroke="var(--series-1)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                label={(props) => <EndLabel {...props} last={last} name="Attempts" />}
              />
              <Line
                dataKey="solves"
                name="Solves"
                stroke="var(--series-2)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
                label={(props) => <EndLabel {...props} last={last} name="Solves" />}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card className="min-w-0">
        <h2 className="mb-3 font-medium">Tokens consumed per day</h2>
        <div style={{ height: CHART_HEIGHT }} role="img" aria-label="Bar chart of tokens consumed per day. The same figures are in the table below.">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis dataKey="date" tickFormatter={formatDay} {...axisProps} />
              <YAxis allowDecimals={false} {...axisProps} axisLine={false} />
              <Tooltip {...tooltipProps} cursor={{ fill: "var(--subtle)" }} />
              <Bar
                dataKey="tokensConsumed"
                name="Tokens"
                fill="var(--series-1)"
                radius={[4, 4, 0, 0]}
                maxBarSize={28}
                isAnimationActive={false}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card className="min-w-0 xl:col-span-2">
        <ChartTable series={series} />
      </Card>
    </div>
  );
}
