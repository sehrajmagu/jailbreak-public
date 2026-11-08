"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { SortHeader, useSorted } from "@/components/admin/sortable";
import { EnabledBadge } from "@/components/status";
import {
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  InlineError,
  Input,
  LoadingRows,
  PageHeader,
  TableWrap,
  tdClass,
} from "@/components/ui";
import { cn, formatNumber, timeAgo } from "@/lib/format";
import { useAdminTeams, useCreateTeam } from "@/lib/hooks/admin";
import type { AdminTeam } from "@/lib/types";

function CreateTeamDialog() {
  const router = useRouter();
  const create = useCreateTeam();
  const [open, setOpen] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    create.mutate(
      {
        name: String(form.get("name")),
        password: String(form.get("password")),
      },
      { onSuccess: (team) => router.push(`/admin/teams/${team.id}`) },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="Create team"
      trigger={
        <Button>
          <Plus className="size-4" aria-hidden /> Create team
        </Button>
      }
    >
      <form onSubmit={submit} className="space-y-3">
        <Field label="Team name">{(id) => <Input id={id} name="name" required />}</Field>
        <Field label="Password">
          {(id) => <Input id={id} name="password" required minLength={8} autoComplete="new-password" />}
        </Field>
        <InlineError error={create.error} />
        <div className="flex justify-end">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating..." : "Create team"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export default function AdminTeamsPage() {
  const teams = useAdminTeams();
  const [search, setSearch] = useState("");
  const matching = teams.data?.filter((t) =>
    `${t.name} ${t.id}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const sort = useSorted<AdminTeam>(matching, "score");

  return (
    <>
      <PageHeader title="Teams" actions={<CreateTeamDialog />} />
      <div className="mb-4 max-w-xs">
        <Field label="Search teams">
          {(id) => (
            <Input
              id={id}
              type="search"
              placeholder="Name or ID"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          )}
        </Field>
      </div>
      {teams.isError && !teams.data ? (
        <ErrorState error={teams.error} onRetry={() => teams.refetch()} />
      ) : !sort.sorted ? (
        <LoadingRows rows={12} />
      ) : sort.sorted.length === 0 ? (
        <EmptyState title="No teams found" description="Try a different search." />
      ) : (
        <TableWrap>
          <caption className="sr-only">Teams. Column headers sort the table.</caption>
          <thead>
            <tr>
              <SortHeader label="Team" column="name" sort={sort} />
              <SortHeader label="Status" column="disabled" sort={sort} />
              <SortHeader label="Tokens used" column="tokensUsed" sort={sort} numeric />
              <SortHeader label="Attempted" column="attempted" sort={sort} numeric />
              <SortHeader label="Solved" column="solved" sort={sort} numeric />
              <SortHeader label="Score" column="score" sort={sort} numeric />
              <SortHeader label="Last active" column="lastActiveAt" sort={sort} />
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {sort.sorted.map((team) => (
              <tr key={team.id} className="hover:bg-subtle">
                <td className={cn(tdClass, "whitespace-nowrap")}>
                  <Link href={`/admin/teams/${team.id}`} className="font-medium underline-offset-4 hover:underline">
                    {team.name}
                  </Link>
                  <span className="ml-2 text-xs text-muted">{team.id}</span>
                </td>
                <td className={tdClass}><EnabledBadge enabled={!team.disabled} /></td>
                <td className={cn(tdClass, "text-right")}>{formatNumber(team.tokensUsed)}</td>
                <td className={cn(tdClass, "text-right")}>{team.attempted}</td>
                <td className={cn(tdClass, "text-right")}>{team.solved}</td>
                <td className={cn(tdClass, "text-right")}>{formatNumber(team.score)}</td>
                <td className={cn(tdClass, "whitespace-nowrap text-muted")}>
                  {team.lastActiveAt ? timeAgo(team.lastActiveAt) : "Never"}
                </td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </>
  );
}
