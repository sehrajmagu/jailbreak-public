"use client";

import Link from "next/link";
import { ResultBadge } from "@/components/status";
import { buttonClass, Card, EmptyState, ErrorState, LoadingRows, PageHeader } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { useSubmissions } from "@/lib/hooks/team";

export default function SubmissionsPage() {
  const submissions = useSubmissions();

  return (
    <>
      <PageHeader title="Submissions" description="Your judged conversations and password attempts." />
      {submissions.isError && !submissions.data ? (
        <ErrorState error={submissions.error} onRetry={() => submissions.refetch()} />
      ) : !submissions.data ? (
        <LoadingRows />
      ) : submissions.data.length === 0 ? (
        <EmptyState
          title="No submissions yet"
          description="Submit a conversation or a password from a bot's page to see the result here."
          action={<Link href="/levels" className={buttonClass("secondary")}>Go to levels</Link>}
        />
      ) : (
        <ul className="space-y-3">
          {submissions.data.map((submission) => (
            <li key={submission.id}>
              <Card>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className="font-medium">Level {submission.level} · {submission.botLabel}</h2>
                    <p className="text-xs text-muted">{formatDateTime(submission.createdAt)}</p>
                  </div>
                  <ResultBadge result={submission.status} />
                </div>
                <p className="mt-2 text-sm text-muted">
                  {submission.reason ?? "The judge is reviewing this conversation."}
                </p>
                <div className="mt-2 flex items-center justify-between gap-2 text-sm">
                  <span className="font-medium">
                    {submission.pointsAwarded === null
                      ? "Points pending"
                      : `${submission.pointsAwarded} points awarded`}
                  </span>
                  <Link
                    href={
                      submission.conversationId
                        ? `/bots/${submission.botId}?conversation=${submission.conversationId}`
                        : `/bots/${submission.botId}`
                    }
                    className="text-muted underline underline-offset-4 hover:text-foreground"
                  >
                    {submission.conversationId ? "View conversation" : "View bot"}
                  </Link>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
