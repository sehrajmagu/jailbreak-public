"use client";

import Link from "next/link";
import { useState } from "react";
import { ResultBadge } from "@/components/status";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  InlineError,
  LoadingRows,
  PageHeader,
  Pagination,
  Select,
  TableWrap,
  tdClass,
  thClass,
} from "@/components/ui";
import { cn, formatDateTime } from "@/lib/format";
import {
  useAdminSubmissions,
  useAdminTeams,
  useJudging,
  useRegradeSubmission,
  useSetJudging,
} from "@/lib/hooks/admin";
import type { AdminSubmission, SubmissionStatus } from "@/lib/types";

const STATUSES: SubmissionStatus[] = ["pending", "grading", "passed", "failed"];

export default function AdminSubmissionsPage() {
  const [status, setStatus] = useState<SubmissionStatus | "">("");
  const [teamId, setTeamId] = useState("");
  const [page, setPage] = useState(1);
  const [regrading, setRegrading] = useState<AdminSubmission | null>(null);
  const teams = useAdminTeams();
  const submissions = useAdminSubmissions({
    status: status || undefined,
    teamId: teamId || undefined,
    page,
  });
  const regrade = useRegradeSubmission();
  const data = submissions.data;

  return (
    <>
      <PageHeader title="Submissions" description="Judge verdicts and password attempts." />
      <JudgingSwitch />
      <div className="mb-4 grid gap-3 sm:max-w-md sm:grid-cols-2">
        <Field label="Status">
          {(id) => (
            <Select
              id={id}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as SubmissionStatus | "");
                setPage(1);
              }}
            >
              <option value="">All statuses</option>
              {STATUSES.map((s) => (
                <option key={s} value={s} className="capitalize">{s[0].toUpperCase() + s.slice(1)}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Team">
          {(id) => (
            <Select
              id={id}
              value={teamId}
              onChange={(e) => {
                setTeamId(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All teams</option>
              {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
          )}
        </Field>
      </div>

      {submissions.isError && !data ? (
        <ErrorState error={submissions.error} onRetry={() => submissions.refetch()} />
      ) : !data ? (
        <LoadingRows rows={10} />
      ) : data.items.length === 0 ? (
        <EmptyState title="No submissions found" description="Try changing the filters." />
      ) : (
        <div className={submissions.isPlaceholderData ? "opacity-60" : undefined}>
          <TableWrap>
            <caption className="sr-only">Submissions, newest first</caption>
            <thead>
              <tr>
                <th scope="col" className={thClass}>Team</th>
                <th scope="col" className={thClass}>Bot</th>
                <th scope="col" className={thClass}>Model</th>
                <th scope="col" className={thClass}>Status</th>
                <th scope="col" className={thClass}>Reason</th>
                <th scope="col" className={cn(thClass, "text-right")}>Points</th>
                <th scope="col" className={thClass}>Submitted</th>
                <th scope="col" className={thClass}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.items.map((s) => {
                // Teams and organisers see an error as "grading", and only an error has a reason yet.
                const busy = s.status === "pending" || (s.status === "grading" && !s.reason);
                return (
                  <tr key={s.id}>
                    <td className={cn(tdClass, "whitespace-nowrap")}>{s.teamName}</td>
                    <td className={cn(tdClass, "whitespace-nowrap")}>Level {s.level} · {s.botLabel}</td>
                    <td className={cn(tdClass, "whitespace-nowrap")}>{s.model}</td>
                    <td className={tdClass}><ResultBadge result={s.status} /></td>
                    <td className={cn(tdClass, "min-w-64 text-muted")}>{s.reason ?? "Waiting for the judge."}</td>
                    <td className={cn(tdClass, "text-right")}>{s.pointsAwarded ?? "-"}</td>
                    <td className={cn(tdClass, "whitespace-nowrap text-muted")}>{formatDateTime(s.createdAt)}</td>
                    <td className={tdClass}>
                      <div className="flex items-center gap-1 whitespace-nowrap">
                        {s.checkType === "judge" ? (
                          <>
                            <Link
                              href={`/admin/conversations/${s.conversationId}`}
                              aria-label={`View conversation for ${s.teamName}, level ${s.level}, ${s.botLabel}`}
                              className="rounded-md px-2 py-1 text-sm underline-offset-4 hover:underline"
                            >
                              View
                            </Link>
                            <Button
                              variant="ghost"
                              disabled={busy}
                              aria-label={`Regrade ${s.teamName}, level ${s.level}, ${s.botLabel}`}
                              onClick={() => {
                                regrade.reset();
                                setRegrading(s);
                              }}
                            >
                              Regrade
                            </Button>
                          </>
                        ) : (
                          <span className="px-2 text-xs text-muted">Answer box</span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </TableWrap>
          <Pagination page={data.page} pageSize={data.pageSize} total={data.total} onPage={setPage} />
        </div>
      )}

      <ConfirmDialog
        open={regrading !== null}
        onOpenChange={(open) => !open && setRegrading(null)}
        title="Regrade this submission?"
        description={
          regrading
            ? `${regrading.teamName}, level ${regrading.level}, ${regrading.botLabel}. The current verdict is replaced, which can change the team's score.`
            : ""
        }
        confirmLabel="Regrade"
        pending={regrade.isPending}
        error={regrade.error}
        onConfirm={() =>
          regrade.mutate(regrading!.id, { onSuccess: () => setRegrading(null) })
        }
      />
    </>
  );
}

/** Turns the judge on or off at once. Teams can't see this, and still see "Grading". */
function JudgingSwitch() {
  const judging = useJudging();
  const setJudging = useSetJudging();
  const [confirming, setConfirming] = useState(false);
  const state = judging.data;

  if (judging.isError && !state) {
    return <div className="mb-4"><ErrorState error={judging.error} onRetry={() => judging.refetch()} /></div>;
  }
  if (!state) return <div className="mb-4"><LoadingRows rows={1} /></div>;

  return (
    <section
      aria-labelledby="judging-heading"
      className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
    >
      <div className="text-sm">
        <h2 id="judging-heading" className="font-semibold">
          Judging is{" "}
          <span className={state.enabled ? "text-good" : "text-bad"}>{state.enabled ? "on" : "off"}</span>
        </h2>
        <p className="mt-0.5 text-muted">
          {state.enabled
            ? "Submissions are sent to the judge in the order they arrived."
            : "Teams can still submit. Nothing is sent to the judge until it's back on."}{" "}
          {state.waiting} waiting
          {state.needsOrganiser > 0 && `, ${state.needsOrganiser} need a regrade`}.
          {state.enabled && state.pausedUntil &&
            ` Paused by a judge rate limit or outage until ${formatDateTime(state.pausedUntil)}.`}
        </p>
        <InlineError error={setJudging.error} />
      </div>
      <Button
        variant={state.enabled ? "secondary" : "primary"}
        disabled={setJudging.isPending}
        aria-pressed={state.enabled}
        onClick={() => (state.enabled ? setConfirming(true) : setJudging.mutate(true))}
      >
        {setJudging.isPending ? "Saving..." : state.enabled ? "Turn judging off" : "Turn judging on"}
      </Button>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Turn judging off?"
        description="Teams can still submit, and see their submissions as grading. Nothing is graded until you turn it back on. A grade already in progress still finishes."
        confirmLabel="Turn off"
        danger
        pending={setJudging.isPending}
        error={setJudging.error}
        onConfirm={() => setJudging.mutate(false, { onSuccess: () => setConfirming(false) })}
      />
    </section>
  );
}
