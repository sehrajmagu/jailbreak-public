"use client";

import Link from "next/link";
import { LeaderboardTable } from "@/components/team/leaderboard-table";
import {
  Card,
  ErrorState,
  LoadingRows,
  PageHeader,
  Skeleton,
  Stat,
  TableWrap,
  tdClass,
  thClass,
} from "@/components/ui";
import { cn, formatNumber, formatRupees, formatUsd } from "@/lib/format";
import { useBudget, useDashboard, useLeaderboard, useMe } from "@/lib/hooks/team";

const MINI_LEADERBOARD_SIZE = 5;

function BudgetCard() {
  const { data: budget, isError } = useBudget();
  if (!budget) {
    return isError ? (
      <Card className="mt-4">
        <p className="text-sm text-muted">Your AI budget can&apos;t be shown right now. You can keep playing.</p>
      </Card>
    ) : (
      <Skeleton className="mt-4 h-24" />
    );
  }
  return (
    <Card className="mt-4">
      <div className="flex justify-between text-sm">
        <span className="font-medium">AI budget</span>
        <span className={budget.remainingUsd > 0 ? "text-muted" : "font-medium text-bad"}>
          {formatUsd(budget.remainingUsd)} left of {formatUsd(budget.budgetUsd)}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label="AI budget left, in US dollars"
        aria-valuemin={0}
        aria-valuemax={budget.budgetUsd}
        aria-valuenow={budget.remainingUsd}
        className="mt-2 h-2 overflow-hidden rounded-full bg-subtle"
      >
        <div
          className="h-full rounded-full bg-primary"
          style={{ width: `${(budget.remainingUsd / Math.max(budget.budgetUsd, 0.01)) * 100}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-muted">
        {formatUsd(budget.spentUsd)} spent. Shared by your team across every model. Can take up to a minute to update.
      </p>
    </Card>
  );
}

export default function DashboardPage() {
  const { data: me } = useMe();
  const dashboard = useDashboard();
  const leaderboard = useLeaderboard();
  const summary = dashboard.data;

  const topEntries = leaderboard.data?.slice(0, MINI_LEADERBOARD_SIZE) ?? [];
  const own = leaderboard.data?.find((e) => e.isOwnTeam);
  const miniEntries = own && own.rank > MINI_LEADERBOARD_SIZE ? [...topEntries, own] : topEntries;

  return (
    <>
      <PageHeader title={me ? `Welcome, ${me.name}` : "Welcome"} description="Your team's standing at a glance." />

      {dashboard.isError ? (
        <ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} />
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {summary ? (
            <>
              <Stat label="Points" value={formatNumber(summary.points)} />
              <Stat
                label="Rank"
                value={summary.rank ? `#${summary.rank}` : "-"}
                hint={`of ${summary.totalTeams} teams`}
              />
              <Stat label="Solved" value={summary.solved} hint={`${summary.attempted} attempted`} />
              <Stat label="Remaining" value={summary.remaining} hint="bots to solve" />
            </>
          ) : (
            Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24" />)
          )}
        </div>
      )}

      {summary && (
        <Card className="mt-4">
          <div className="flex justify-between text-sm">
            <span className="font-medium">Progress</span>
            <span className="text-muted">
              {summary.solved} of {summary.solved + summary.remaining} solved
            </span>
          </div>
          <div
            role="progressbar"
            aria-label="Bots solved"
            aria-valuemin={0}
            aria-valuemax={summary.solved + summary.remaining}
            aria-valuenow={summary.solved}
            className="mt-2 h-2 overflow-hidden rounded-full bg-subtle"
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{
                width: `${(summary.solved / Math.max(1, summary.solved + summary.remaining)) * 100}%`,
              }}
            />
          </div>
        </Card>
      )}

      <BudgetCard />

      {summary && (
        <section className="mt-8">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold">Prizes</h2>
            <Link href="/levels" className="text-sm text-muted underline underline-offset-4 hover:text-foreground">
              View levels
            </Link>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Levels completed" value={`${summary.levelsCompleted} of ${summary.totalLevels}`} />
            <Stat label="Prize earned so far" value={formatRupees(summary.prizeEarned)} />
          </div>
        </section>
      )}

      {summary && (
        <section className="mt-8">
          <h2 className="mb-3 font-semibold">Token usage by model</h2>
          <TableWrap>
            <caption className="sr-only">Tokens your team has used on each model</caption>
            <thead>
              <tr>
                <th scope="col" className={thClass}>Model</th>
                <th scope="col" className={cn(thClass, "text-right")}>Used</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {summary.usageByModel.map((row) => (
                <tr key={row.model}>
                  <td className={tdClass}>{row.model}</td>
                  <td className={cn(tdClass, "text-right")}>{formatNumber(row.tokensUsed)}</td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </section>
      )}

      <section className="mt-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">Leaderboard</h2>
          <Link href="/leaderboard" className="text-sm text-muted underline underline-offset-4 hover:text-foreground">
            View all
          </Link>
        </div>
        {leaderboard.isError && !leaderboard.data ? (
          <ErrorState error={leaderboard.error} onRetry={() => leaderboard.refetch()} />
        ) : leaderboard.data ? (
          <LeaderboardTable entries={miniEntries} />
        ) : (
          <LoadingRows rows={5} />
        )}
      </section>
    </>
  );
}
