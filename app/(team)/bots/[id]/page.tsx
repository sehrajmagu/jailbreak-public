"use client";

import { ArrowLeft, Plus, Send } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { BotStatusBadge, ResultBadge } from "@/components/status";
import { ChatPanel } from "@/components/team/chat-panel";
import { LevelProgressSummary } from "@/components/team/level-progress";
import {
  Button,
  buttonClass,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  InlineError,
  Input,
  LoadingRows,
  Skeleton,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import { cn, timeAgo } from "@/lib/format";
import {
  useBot,
  useConversation,
  useConversations,
  useLevels,
  useSubmitAnswer,
  useSubmitConversation,
} from "@/lib/hooks/team";
import type { BotDetail } from "@/lib/types";

function AnswerBox({ bot }: { bot: BotDetail }) {
  const submit = useSubmitAnswer(bot.id);
  const [answer, setAnswer] = useState("");
  const exhausted = bot.wrongAnswersUsed >= bot.maxWrongAnswers;

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    submit.mutate(answer, { onSuccess: (result) => result.correct && setAnswer("") });
  }

  return (
    <Card>
      <form onSubmit={onSubmit} className="space-y-3">
        <Field
          label="Enter password"
          hint={`${bot.wrongAnswersUsed} of ${bot.maxWrongAnswers} wrong answers used`}
        >
          {(id) => (
            <Input
              id={id}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              disabled={exhausted}
              required
            />
          )}
        </Field>
        <InlineError error={submit.error} />
        {submit.data && (
          <p role="status" className={submit.data.correct ? "text-sm font-medium text-good" : "text-sm font-medium text-bad"}>
            {submit.data.correct
              ? `Correct. ${submit.data.pointsAwarded} points awarded.`
              : "Incorrect password."}
          </p>
        )}
        {exhausted && (
          <p role="status" className="text-sm font-medium text-bad">
            The answer box is closed for this bot.
          </p>
        )}
        <Button type="submit" variant="secondary" className="w-full" disabled={submit.isPending || exhausted}>
          {submit.isPending ? "Checking..." : "Check password"}
        </Button>
      </form>
    </Card>
  );
}

function SubmissionResult({ conversationId }: { conversationId: string }) {
  const { data } = useConversation(conversationId);
  const submission = data?.submission;
  if (!submission) return null;
  return (
    <Card>
      <div aria-live="polite">
        <div className="flex items-center justify-between gap-2">
          <h2 className="font-medium">Submission</h2>
          <ResultBadge result={submission.status} />
        </div>
        {submission.reason ? (
          <>
            <p className="mt-2 text-sm">{submission.reason}</p>
            <p className="mt-2 text-sm font-medium">
              {submission.pointsAwarded ?? 0} points awarded
            </p>
          </>
        ) : (
          <p className="mt-2 text-sm text-muted">The judge is reviewing this conversation.</p>
        )}
      </div>
    </Card>
  );
}

function ConversationHistory({
  botId,
  activeId,
  onSelect,
}: {
  botId: string;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const conversations = useConversations(botId);
  return (
    <Card>
      <h2 className="mb-2 font-medium">Previous conversations</h2>
      {conversations.isLoading ? (
        <LoadingRows rows={3} />
      ) : conversations.isError ? (
        <ErrorState error={conversations.error} onRetry={() => conversations.refetch()} />
      ) : conversations.data?.length === 0 ? (
        <p className="text-sm text-muted">None yet. Your conversations will appear here.</p>
      ) : (
        <ul className="-mx-1.5 max-h-72 space-y-1 overflow-y-auto p-0.5">
          {conversations.data?.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={c.id === activeId ? "true" : undefined}
                className={cn(
                  "w-full rounded-md px-1.5 py-1.5 text-left text-sm hover:bg-subtle",
                  c.id === activeId && "bg-subtle",
                )}
              >
                <span className="block truncate">{c.title}</span>
                <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                  {timeAgo(c.updatedAt)} · {c.messageCount} messages
                  {c.submissionStatus && <ResultBadge result={c.submissionStatus} />}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function BotWorkspace() {
  const { id } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const bot = useBot(id);
  const levels = useLevels();
  const [conversationId, setConversationId] = useState<string | null>(
    searchParams.get("conversation"),
  );
  // Changing this remounts the chat, so typed text and errors don't carry over.
  const [chatKey, setChatKey] = useState(0);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const conversation = useConversation(conversationId);
  const submit = useSubmitConversation(id);

  function openConversation(next: string | null) {
    setConversationId(next);
    setChatKey((key) => key + 1);
  }

  if (bot.isError) {
    const code = bot.error instanceof ApiError ? bot.error.code : null;
    if (code === "forbidden" || code === "not_found") {
      return (
        <EmptyState
          title={code === "forbidden" ? "This level is locked" : "Bot not found"}
          description={code === "forbidden" ? bot.error.message : undefined}
          action={<Link href="/levels" className={buttonClass("secondary")}>Back to levels</Link>}
        />
      );
    }
    return <ErrorState error={bot.error} onRetry={() => bot.refetch()} />;
  }
  if (!bot.data) return <Skeleton className="h-96 w-full" />;
  const b = bot.data;
  const level = levels.data?.find((l) => l.level === b.level);
  const isJudge = b.checkType === "judge";

  const canSubmit =
    conversationId !== null &&
    Boolean(conversation.data?.messages.length) &&
    !conversation.data?.submission;

  return (
    <>
      <Link href="/levels" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-foreground">
        <ArrowLeft className="size-4" aria-hidden /> Levels
      </Link>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted">
            Level {b.level}: {b.levelName} · {b.model} · {b.points} pts
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">{b.label}</h1>
        </div>
        <BotStatusBadge status={b.status} />
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_300px] lg:grid-rows-[auto_1fr]">
        <div className="space-y-4 lg:col-start-2 lg:row-start-1">
          <Card>
            <h2 className="font-medium">About this bot</h2>
            <p className="mt-1 text-sm text-muted">{b.description}</p>
            <h2 className="mt-3 font-medium">Goal</h2>
            <p className="mt-1 text-sm text-muted">{b.goal}</p>
          </Card>
          {level && (
            <Card>
              <LevelProgressSummary level={level} />
            </Card>
          )}
          {!isJudge && <AnswerBox bot={b} />}
          {isJudge && conversationId && <SubmissionResult conversationId={conversationId} />}
        </div>

        <div className="min-w-0 space-y-3 lg:col-start-1 lg:row-span-2 lg:row-start-1">
          <div className="flex flex-wrap justify-between gap-2">
            <Button variant="secondary" onClick={() => openConversation(null)}>
              <Plus className="size-4" aria-hidden /> New conversation
            </Button>
            {isJudge && (
              <Button disabled={!canSubmit} onClick={() => setConfirmOpen(true)}>
                <Send className="size-4" aria-hidden /> Submit for grading
              </Button>
            )}
          </div>
          <ChatPanel
            key={chatKey}
            botId={id}
            botLabel={b.label}
            model={b.model}
            wrongAnswersUsedUp={!isJudge && b.wrongAnswersUsed >= b.maxWrongAnswers}
            conversationId={conversationId}
            onConversationStarted={setConversationId}
          />
        </div>

        <div className="lg:col-start-2 lg:row-start-2">
          <ConversationHistory botId={id} activeId={conversationId} onSelect={openConversation} />
        </div>
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          setConfirmOpen(open);
          if (!open) submit.reset();
        }}
        title="Submit this conversation for grading?"
        description="The judge will review the whole conversation. Once submitted, it can't be continued."
        confirmLabel="Submit"
        pending={submit.isPending}
        error={submit.error}
        onConfirm={() =>
          submit.mutate(conversationId!, { onSuccess: () => setConfirmOpen(false) })
        }
      />
    </>
  );
}

export default function BotPage() {
  return (
    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
      <BotWorkspace />
    </Suspense>
  );
}
