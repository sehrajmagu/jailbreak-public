"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { BotStatusBadge, EnabledBadge } from "@/components/status";
import {
  Button,
  buttonClass,
  Card,
  ConfirmDialog,
  ErrorState,
  Field,
  InlineError,
  Input,
  LoadingRows,
  PageHeader,
  Stat,
  TableWrap,
  tdClass,
  thClass,
} from "@/components/ui";
import { cn, formatNumber } from "@/lib/format";
import {
  useAdminTeam,
  useResetTeamPassword,
  useUpdateTeam,
} from "@/lib/hooks/admin";
import type { AdminTeamDetail } from "@/lib/types";

function EditTeamCard({ team }: { team: AdminTeamDetail }) {
  const update = useUpdateTeam(team.id);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    update.mutate({
      name: String(form.get("name")),
    });
  }
  return (
    <Card>
      <h2 className="mb-3 font-medium">Edit team</h2>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Team name">
          {(id) => <Input id={id} name="name" defaultValue={team.name} key={team.name} required />}
        </Field>
        <InlineError error={update.error} />
        <div className="flex items-center gap-3">
          <Button type="submit" variant="secondary" disabled={update.isPending}>
            {update.isPending ? "Saving..." : "Save changes"}
          </Button>
          {update.isSuccess && <span role="status" className="text-sm text-good">Saved</span>}
        </div>
      </form>
    </Card>
  );
}

function AccessCard({ team }: { team: AdminTeamDetail }) {
  const update = useUpdateTeam(team.id);
  const reset = useResetTeamPassword(team.id);
  const [confirm, setConfirm] = useState<"reset" | "disable" | null>(null);

  return (
    <Card>
      <h2 className="mb-3 font-medium">Access</h2>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => setConfirm("reset")}>Reset password</Button>
        <Button
          variant={team.disabled ? "secondary" : "danger"}
          onClick={() => setConfirm("disable")}
        >
          {team.disabled ? "Enable team" : "Disable team"}
        </Button>
      </div>
      {reset.data && (
        <p role="status" className="mt-3 rounded-md bg-subtle p-2 text-sm">
          Temporary password (shown once):{" "}
          <code className="font-mono font-medium">{reset.data.temporaryPassword}</code>
        </p>
      )}
      <ConfirmDialog
        open={confirm === "reset"}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={`Reset the password for ${team.name}?`}
        description="The current password stops working right away. A new one is shown once."
        confirmLabel="Reset password"
        pending={reset.isPending}
        error={reset.error}
        onConfirm={() => reset.mutate(undefined, { onSuccess: () => setConfirm(null) })}
      />
      <ConfirmDialog
        open={confirm === "disable"}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={team.disabled ? `Enable ${team.name}?` : `Disable ${team.name}?`}
        description={
          team.disabled
            ? "The team can sign in again and returns to the leaderboard."
            : "The team is signed out, can't sign in and leaves the leaderboard. Its data is kept."
        }
        confirmLabel={team.disabled ? "Enable team" : "Disable team"}
        danger={!team.disabled}
        pending={update.isPending}
        error={update.error}
        onConfirm={() =>
          update.mutate({ disabled: !team.disabled }, { onSuccess: () => setConfirm(null) })
        }
      />
    </Card>
  );
}

export default function AdminTeamDetailPage() {
  const { id } = useParams<{ id: string }>();
  const team = useAdminTeam(id);
  const data = team.data;

  if (team.isError && !data) return <ErrorState error={team.error} onRetry={() => team.refetch()} />;
  if (!data) return <LoadingRows rows={8} />;

  return (
    <>
      <Link href="/admin/teams" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> Teams
      </Link>
      <PageHeader
        title={data.name}
        description={<span className="inline-flex items-center gap-2">{data.id} <EnabledBadge enabled={!data.disabled} /></span>}
        actions={
          <Link href={`/admin/conversations?team=${data.id}`} className={buttonClass("secondary")}>
            View conversations
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Stat label="Score" value={formatNumber(data.score)} hint={data.rank ? `Rank #${data.rank}` : "Not ranked"} />
        <Stat label="Solved" value={data.solved} hint={`${data.attempted} attempted`} />
        <Stat label="Tokens used" value={formatNumber(data.tokensUsed)} hint="all models" />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <EditTeamCard team={data} />
        <AccessCard team={data} />
      </div>

      <h2 className="mt-8 mb-3 font-semibold">Bot progress</h2>
      <TableWrap>
        <caption className="sr-only">Status of each bot for this team</caption>
        <thead>
          <tr>
            <th scope="col" className={thClass}>Level</th>
            <th scope="col" className={thClass}>Bot</th>
            <th scope="col" className={thClass}>Model</th>
            <th scope="col" className={thClass}>Status</th>
            <th scope="col" className={cn(thClass, "text-right")}>Conversations</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {data.progress.map((row) => (
            <tr key={row.botId}>
              <td className={tdClass}>{row.level}</td>
              <td className={cn(tdClass, "whitespace-nowrap font-medium")}>{row.label}</td>
              <td className={cn(tdClass, "whitespace-nowrap")}>{row.model}</td>
              <td className={tdClass}><BotStatusBadge status={row.status} /></td>
              <td className={cn(tdClass, "text-right")}>{row.conversations}</td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </>
  );
}
