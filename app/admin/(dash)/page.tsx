"use client";

import { OverviewCharts } from "@/components/admin/overview-charts";
import { ErrorState, PageHeader, Skeleton, Stat } from "@/components/ui";
import { formatNumber } from "@/lib/format";
import { useAdminOverview } from "@/lib/hooks/admin";

export default function AdminOverviewPage() {
  const overview = useAdminOverview();
  const data = overview.data;

  return (
    <>
      <PageHeader title="Overview" description="Event activity across all teams." />
      {overview.isError && !data ? (
        <ErrorState error={overview.error} onRetry={() => overview.refetch()} />
      ) : !data ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Stat label="Total teams" value={data.totalTeams} />
            <Stat label="Active teams" value={data.activeTeams} hint="in the last 24 hours" />
            <Stat label="Tokens consumed" value={formatNumber(data.tokensConsumed)} />
            <Stat label="Total attempts" value={formatNumber(data.totalAttempts)} hint="conversations" />
            <Stat label="Total solves" value={formatNumber(data.totalSolves)} />
          </div>
          <OverviewCharts series={data.series} />
        </>
      )}
    </>
  );
}
