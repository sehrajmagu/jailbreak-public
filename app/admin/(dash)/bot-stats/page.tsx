"use client";

import { useState } from "react";
import { SortHeader, useSorted } from "@/components/admin/sortable";
import {
  EmptyState,
  ErrorState,
  Field,
  LoadingRows,
  PageHeader,
  Select,
  TableWrap,
  tdClass,
} from "@/components/ui";
import { LEVELS } from "@/lib/constants";
import { cn, formatNumber } from "@/lib/format";
import { useBotStats } from "@/lib/hooks/admin";
import type { BotStat } from "@/lib/types";

export default function BotStatsPage() {
  const stats = useBotStats();
  const [level, setLevel] = useState("");
  const sort = useSorted<BotStat>(
    stats.data?.filter((s) => !level || s.level === Number(level)),
    "attempts",
  );

  return (
    <>
      <PageHeader
        title="Bot stats"
        description="An attempt is one conversation. Solve rate is solving teams divided by teams that tried."
      />
      <div className="mb-4 max-w-xs">
        <Field label="Level">
          {(id) => (
            <Select id={id} value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="">All levels</option>
              {LEVELS.map((l) => <option key={l.level} value={l.level}>Level {l.level}: {l.name}</option>)}
            </Select>
          )}
        </Field>
      </div>
      {stats.isError && !stats.data ? (
        <ErrorState error={stats.error} onRetry={() => stats.refetch()} />
      ) : !sort.sorted ? (
        <LoadingRows rows={12} />
      ) : sort.sorted.length === 0 ? (
        <EmptyState title="No bots in this level" />
      ) : (
        <TableWrap>
          <caption className="sr-only">Per-bot statistics. Column headers sort the table.</caption>
          <thead>
            <tr>
              <SortHeader label="Level" column="level" sort={sort} numeric />
              <SortHeader label="Bot" column="label" sort={sort} />
              <SortHeader label="Model" column="model" sort={sort} />
              <SortHeader label="Attempts" column="attempts" sort={sort} numeric />
              <SortHeader label="Solves" column="solves" sort={sort} numeric />
              <SortHeader label="Solve rate" column="solveRate" sort={sort} numeric />
              <SortHeader label="Avg attempts to solve" column="avgAttemptsToSolve" sort={sort} numeric />
              <SortHeader label="Tokens" column="tokensUsed" sort={sort} numeric />
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {sort.sorted.map((s) => (
              <tr key={s.botId}>
                <td className={cn(tdClass, "text-right")}>{s.level}</td>
                <td className={cn(tdClass, "whitespace-nowrap font-medium")}>
                  {s.label}
                  {!s.enabled && <span className="ml-2 text-xs font-normal text-muted">Disabled</span>}
                </td>
                <td className={cn(tdClass, "whitespace-nowrap")}>{s.model}</td>
                <td className={cn(tdClass, "text-right")}>{s.attempts}</td>
                <td className={cn(tdClass, "text-right")}>{s.solves}</td>
                <td className={cn(tdClass, "text-right")}>{Math.round(s.solveRate * 100)}%</td>
                <td className={cn(tdClass, "text-right")}>
                  {s.avgAttemptsToSolve === null ? "-" : s.avgAttemptsToSolve.toFixed(1)}
                </td>
                <td className={cn(tdClass, "text-right")}>{formatNumber(s.tokensUsed)}</td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </>
  );
}
