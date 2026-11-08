"use client";

import { LeaderboardTable } from "@/components/team/leaderboard-table";
import { EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/ui";
import { useLeaderboard } from "@/lib/hooks/team";

export default function LeaderboardPage() {
  const leaderboard = useLeaderboard();
  const updatedAt = leaderboard.dataUpdatedAt
    ? new Date(leaderboard.dataUpdatedAt).toLocaleTimeString("en-IN")
    : null;

  return (
    <>
      <PageHeader
        title="Leaderboard"
        description={
          updatedAt ? `Refreshes automatically. Last updated ${updatedAt}.` : "Refreshes automatically."
        }
      />
      {leaderboard.isError && !leaderboard.data ? (
        <ErrorState error={leaderboard.error} onRetry={() => leaderboard.refetch()} />
      ) : !leaderboard.data ? (
        <LoadingRows rows={12} />
      ) : leaderboard.data.length === 0 ? (
        <EmptyState title="No teams on the board yet" />
      ) : (
        <LeaderboardTable entries={leaderboard.data} />
      )}
    </>
  );
}
