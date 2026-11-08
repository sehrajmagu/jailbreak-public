"use client";

import { useState } from "react";
import { Badge, Button, ConfirmDialog, ErrorState, LoadingRows, PageHeader, Stat, TableWrap, tdClass, thClass } from "@/components/ui";
import { cn, formatDateTime, formatRupees } from "@/lib/format";
import { useAdminPrizes, useSetLevelOverride } from "@/lib/hooks/admin";
import type { AdminLevelCompletion, LevelOverride } from "@/lib/types";

interface PendingOverride {
  teamId: string;
  teamName: string;
  level: number;
  override: LevelOverride | null;
}

const OVERRIDE_TEXT: Record<"granted" | "revoked" | "clear", { verb: string; effect: string }> = {
  granted: { verb: "Grant", effect: "The level counts as complete regardless of solves, and its prize is owed." },
  revoked: { verb: "Revoke", effect: "The level stops counting as complete, no prize is owed, and later levels may lock again." },
  clear: { verb: "Clear override on", effect: "The team's solves decide whether the level is complete again." },
};

function LevelCell({
  completion,
  onChange,
}: {
  completion: AdminLevelCompletion;
  onChange: (override: LevelOverride | null) => void;
}) {
  return (
    <td className={cn(tdClass, "align-top whitespace-nowrap")}>
      <div className="flex flex-col items-start gap-1">
        {completion.complete ? (
          <Badge className="text-good">Complete</Badge>
        ) : (
          <span className="text-xs text-muted">
            {Math.min(completion.solved, completion.required)} of {completion.required}
          </span>
        )}
        {completion.complete && (
          <span className="text-xs text-muted">
            {formatRupees(completion.prizeAwarded)}
            {completion.completedAt && ` · ${formatDateTime(completion.completedAt)}`}
          </span>
        )}
        {completion.override && (
          <span className="text-xs font-medium text-warn">
            {completion.override === "granted" ? "Granted by admin" : "Revoked by admin"}
          </span>
        )}
        {completion.override ? (
          <Button variant="ghost" className="min-h-0 px-1 py-0.5 text-xs" onClick={() => onChange(null)}>
            Clear override
          </Button>
        ) : completion.complete ? (
          <Button variant="ghost" className="min-h-0 px-1 py-0.5 text-xs" onClick={() => onChange("revoked")}>
            Revoke
          </Button>
        ) : (
          <Button variant="ghost" className="min-h-0 px-1 py-0.5 text-xs" onClick={() => onChange("granted")}>
            Grant
          </Button>
        )}
      </div>
    </td>
  );
}

export default function AdminPrizesPage() {
  const prizes = useAdminPrizes();
  const setOverride = useSetLevelOverride();
  const [pending, setPending] = useState<PendingOverride | null>(null);
  const data = prizes.data;
  const text = pending ? OVERRIDE_TEXT[pending.override ?? "clear"] : null;

  if (prizes.isError && !data) return <ErrorState error={prizes.error} onRetry={() => prizes.refetch()} />;
  if (!data) return <LoadingRows rows={12} />;

  return (
    <>
      <PageHeader title="Prizes" description="Which teams completed which levels, and what they're owed." />
      <div className="mb-4 grid grid-cols-2 gap-3">
        <Stat label="Total prize owed" value={formatRupees(data.totalPrizeOwed)} />
        <Stat
          label="Teams with a prize"
          value={data.teams.filter((t) => t.prizeOwed > 0).length}
          hint={data.requireLevelsInOrder ? "Levels unlock in order" : "All levels open"}
        />
      </div>

      <TableWrap>
        <caption className="sr-only">Level completion and prize owed for each team</caption>
        <thead>
          <tr>
            <th scope="col" className={thClass}>Team</th>
            <th scope="col" className={cn(thClass, "text-right")}>Prize owed</th>
            {data.levels.map((l) => (
              <th key={l.level} scope="col" className={thClass}>
                <span title={l.name}>Level {l.level}</span>
                <span className="block font-normal">
                  {formatRupees(l.prizeAmount)}, any {l.requiredBots}
                  {l.maxWinners !== null && `, first ${l.maxWinners} teams`}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {data.teams.map((team) => (
            <tr key={team.teamId}>
              <td className={cn(tdClass, "align-top font-medium whitespace-nowrap")}>{team.teamName}</td>
              <td className={cn(tdClass, "align-top text-right font-medium whitespace-nowrap")}>
                {formatRupees(team.prizeOwed)}
              </td>
              {team.levels.map((completion) => (
                <LevelCell
                  key={completion.level}
                  completion={completion}
                  onChange={(override) => {
                    setOverride.reset();
                    setPending({ teamId: team.teamId, teamName: team.teamName, level: completion.level, override });
                  }}
                />
              ))}
            </tr>
          ))}
        </tbody>
      </TableWrap>

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => !open && setPending(null)}
        title={pending && text ? `${text.verb} level ${pending.level} for ${pending.teamName}?` : ""}
        description={text?.effect ?? ""}
        confirmLabel={text?.verb.split(" ")[0] ?? "Confirm"}
        danger={pending?.override === "revoked"}
        pending={setOverride.isPending}
        error={setOverride.error}
        onConfirm={() =>
          setOverride.mutate(pending!, { onSuccess: () => setPending(null) })
        }
      />
    </>
  );
}
