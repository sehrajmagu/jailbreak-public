"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ResultBadge } from "@/components/status";
import {
  EmptyState,
  ErrorState,
  Field,
  LoadingRows,
  PageHeader,
  Pagination,
  Select,
  TableWrap,
  tdClass,
  thClass,
} from "@/components/ui";
import { MODELS } from "@/lib/constants";
import { cn, formatDateTime, formatNumber } from "@/lib/format";
import { useAdminBots, useAdminConversations, useAdminTeams } from "@/lib/hooks/admin";
import type { ConversationResult, ModelName } from "@/lib/types";

const RESULTS: { id: ConversationResult; label: string }[] = [
  { id: "not_submitted", label: "Not submitted" },
  { id: "answer_bot", label: "Answer box" },
  { id: "pending", label: "Pending" },
  { id: "grading", label: "Grading" },
  { id: "passed", label: "Passed" },
  { id: "failed", label: "Failed" },
];

function ConversationsView() {
  const searchParams = useSearchParams();
  const [teamId, setTeamId] = useState(searchParams.get("team") ?? "");
  const [botId, setBotId] = useState("");
  const [model, setModel] = useState<ModelName | "">("");
  const [result, setResult] = useState<ConversationResult | "">("");
  const [page, setPage] = useState(1);

  const teams = useAdminTeams();
  const bots = useAdminBots();
  const conversations = useAdminConversations({
    teamId: teamId || undefined,
    botId: botId || undefined,
    model: model || undefined,
    result: result || undefined,
    page,
  });

  function changed<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      setPage(1);
    };
  }

  const data = conversations.data;

  return (
    <>
      <PageHeader title="Conversations" description="Full logs across all teams, including failed attempts." />
      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Team">
          {(id) => (
            <Select id={id} value={teamId} onChange={(e) => changed(setTeamId)(e.target.value)}>
              <option value="">All teams</option>
              {teams.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Bot">
          {(id) => (
            <Select id={id} value={botId} onChange={(e) => changed(setBotId)(e.target.value)}>
              <option value="">All bots</option>
              {bots.data?.map((b) => <option key={b.id} value={b.id}>Level {b.level} · {b.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Model">
          {(id) => (
            <Select id={id} value={model} onChange={(e) => changed(setModel)(e.target.value as ModelName | "")}>
              <option value="">All models</option>
              {MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Result">
          {(id) => (
            <Select id={id} value={result} onChange={(e) => changed(setResult)(e.target.value as ConversationResult | "")}>
              <option value="">All results</option>
              {RESULTS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </Select>
          )}
        </Field>
      </div>

      {conversations.isError && !data ? (
        <ErrorState error={conversations.error} onRetry={() => conversations.refetch()} />
      ) : !data ? (
        <LoadingRows rows={10} />
      ) : data.items.length === 0 ? (
        <EmptyState title="No conversations found" description="Try changing the filters." />
      ) : (
        <div className={conversations.isPlaceholderData ? "opacity-60" : undefined}>
          <TableWrap>
            <caption className="sr-only">Conversations, newest first</caption>
            <thead>
              <tr>
                <th scope="col" className={thClass}>Conversation</th>
                <th scope="col" className={thClass}>Team</th>
                <th scope="col" className={thClass}>Bot</th>
                <th scope="col" className={thClass}>Model</th>
                <th scope="col" className={thClass}>Result</th>
                <th scope="col" className={cn(thClass, "text-right")}>Tokens</th>
                <th scope="col" className={thClass}>Started</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {data.items.map((c) => (
                <tr key={c.id} className="hover:bg-subtle">
                  <td className={cn(tdClass, "max-w-64 truncate")}>
                    <Link href={`/admin/conversations/${c.id}`} className="font-medium underline-offset-4 hover:underline">
                      {c.title}
                    </Link>
                  </td>
                  <td className={cn(tdClass, "whitespace-nowrap")}>{c.teamName}</td>
                  <td className={cn(tdClass, "whitespace-nowrap")}>Level {c.level} · {c.botLabel}</td>
                  <td className={cn(tdClass, "whitespace-nowrap")}>{c.model}</td>
                  <td className={tdClass}><ResultBadge result={c.result} /></td>
                  <td className={cn(tdClass, "text-right")}>{formatNumber(c.tokens)}</td>
                  <td className={cn(tdClass, "whitespace-nowrap text-muted")}>{formatDateTime(c.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
          <Pagination page={data.page} pageSize={data.pageSize} total={data.total} onPage={setPage} />
        </div>
      )}
    </>
  );
}

export default function AdminConversationsPage() {
  return (
    <Suspense fallback={<LoadingRows rows={10} />}>
      <ConversationsView />
    </Suspense>
  );
}
