"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { MessageBubble } from "@/components/message-list";
import { ResultBadge } from "@/components/status";
import { Card, EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/ui";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useAdminConversation } from "@/lib/hooks/admin";

export default function AdminConversationPage() {
  const { id } = useParams<{ id: string }>();
  const conversation = useAdminConversation(id);
  const data = conversation.data;

  if (conversation.isError && !data) {
    return <ErrorState error={conversation.error} onRetry={() => conversation.refetch()} />;
  }
  if (!data) return <LoadingRows rows={8} />;

  const toolCallCount = data.messages.reduce((sum, m) => sum + m.toolCalls.length, 0);

  return (
    <>
      <Link href="/admin/conversations" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> Conversations
      </Link>
      <PageHeader
        title={`Level ${data.level} · ${data.botLabel}`}
        description={`${data.teamName} · ${data.model} · started ${formatDateTime(data.createdAt)}`}
        actions={<ResultBadge result={data.result} />}
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section aria-label="Messages" className="order-2 min-w-0 space-y-4 rounded-lg border border-border p-4 lg:order-1">
          {data.messages.length === 0 ? (
            <EmptyState title="No messages" />
          ) : (
            data.messages.map((message) => (
              <MessageBubble key={message.id} {...message} failed={message.status === "failed"} />
            ))
          )}
        </section>
        <div className="order-1 space-y-4 lg:order-2">
          <Card>
            <h2 className="font-medium">Usage</h2>
            <dl className="mt-2 space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted">Tokens</dt>
                <dd>{formatNumber(data.tokens)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Messages</dt>
                <dd>{data.messageCount}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Tool calls</dt>
                <dd>{toolCallCount}</dd>
              </div>
            </dl>
          </Card>
          <Card>
            <h2 className="font-medium">Judge verdict</h2>
            {data.verdict ? (
              <div className="mt-2 space-y-2 text-sm" aria-live="polite">
                <ResultBadge result={data.verdict.status} />
                <p>{data.verdict.reason ?? "Grading is in progress."}</p>
                {data.verdict.pointsAwarded !== null && (
                  <p className="font-medium">{data.verdict.pointsAwarded} points awarded</p>
                )}
              </div>
            ) : (
              <p className="mt-2 text-sm text-muted">
                {data.result === "answer_bot"
                  ? "This bot is checked with the answer box. See Submissions for the team's attempts."
                  : "This conversation was not submitted."}
              </p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
