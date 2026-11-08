import { cn, formatNumber } from "@/lib/format";
import type { LeaderboardEntry } from "@/lib/types";
import { Badge, TableWrap, tdClass, thClass } from "@/components/ui";

export function LeaderboardTable({ entries }: { entries: LeaderboardEntry[] }) {
  return (
    <TableWrap>
      <caption className="sr-only">Team ranking by score</caption>
      <thead>
        <tr>
          <th scope="col" className={cn(thClass, "w-14")}>Rank</th>
          <th scope="col" className={thClass}>Team</th>
          <th scope="col" className={cn(thClass, "text-right")}>Solved</th>
          <th scope="col" className={cn(thClass, "text-right")}>Score</th>
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {entries.map((entry) => (
          <tr
            key={entry.teamId}
            aria-current={entry.isOwnTeam ? "true" : undefined}
            className={entry.isOwnTeam ? "bg-highlight font-medium" : undefined}
          >
            <td className={tdClass}>{entry.rank}</td>
            <td className={tdClass}>
              <span className="inline-flex items-center gap-2">
                {entry.teamName}
                {entry.isOwnTeam && <Badge>Your team</Badge>}
              </span>
            </td>
            <td className={cn(tdClass, "text-right")}>{entry.solved}</td>
            <td className={cn(tdClass, "text-right")}>{formatNumber(entry.score)}</td>
          </tr>
        ))}
      </tbody>
    </TableWrap>
  );
}
