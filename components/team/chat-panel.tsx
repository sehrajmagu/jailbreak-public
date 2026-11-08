"use client";

import { Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { MessageBubble } from "@/components/message-list";
import { Button, InlineError, LoadingRows, Textarea } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useConversation, useSendMessage } from "@/lib/hooks/team";

function useCountdown(until: number | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!until) return;
    const tick = () => setNow(Date.now());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [until]);
  return until ? Math.max(0, Math.ceil((until - now) / 1000)) : 0;
}

export function ChatPanel({
  botId,
  botLabel,
  model,
  wrongAnswersUsedUp,
  conversationId,
  onConversationStarted,
}: {
  botId: string;
  botLabel: string;
  model: string;
  wrongAnswersUsedUp: boolean;
  conversationId: string | null;
  onConversationStarted: (id: string | null) => void;
}) {
  const conversation = useConversation(conversationId);
  const { send, draft, error, streamFailure, clearError } = useSendMessage(
    botId,
    conversationId,
    onConversationStarted,
  );
  const [input, setInput] = useState("");
  const [rateLimitedUntil, setRateLimitedUntil] = useState<number | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const rateLimitSeconds = useCountdown(rateLimitedUntil);
  const submitted = Boolean(conversation.data?.submission);
  const streaming = draft !== null;

  useEffect(() => {
    if (error instanceof ApiError && error.code === "rate_limited") {
      setRateLimitedUntil(Date.now() + (error.retryAfterSeconds ?? 30) * 1000);
    }
  }, [error]);

  // The server saves messages before streaming, so a refetch can already hold
  // the ones the draft is showing.
  const saved = (conversation.data?.messages ?? []).filter(
    (m) => !draft || (m.id !== draft.user.id && m.id !== draft.assistantId),
  );

  const lastLength = draft?.content.length ?? 0;
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [saved.length, lastLength, draft?.toolCalls.length, draft?.user.id]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const content = input.trim();
    if (!content || streaming || wrongAnswersUsedUp || rateLimitSeconds > 0 || submitted) return;
    setInput("");
    const sent = await send(content);
    // Give the text back so it doesn't have to be retyped.
    if (!sent) setInput(content);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  const disabled = streaming || wrongAnswersUsedUp || submitted || rateLimitSeconds > 0;
  const showGenericError =
    Boolean(error) &&
    !(error instanceof ApiError && error.code === "rate_limited");

  return (
    <div className="flex h-[70vh] min-h-96 flex-col overflow-hidden rounded-lg border border-border bg-background">
      <div className="border-b border-border bg-surface px-4 py-2.5 text-sm">
        <span className="font-medium">{botLabel}</span>
        <span className="text-muted"> · {model}</span>
      </div>
      <div
        ref={logRef}
        role="log"
        aria-label="Conversation"
        aria-live="polite"
        tabIndex={0}
        className="flex-1 space-y-4 overflow-y-auto p-4"
      >
        {conversationId && conversation.isLoading && !draft ? (
          <LoadingRows rows={4} />
        ) : conversation.isError && !draft ? (
          <InlineError error={conversation.error} />
        ) : saved.length === 0 && !draft ? (
          <p className="py-10 text-center text-sm text-muted">
            No messages yet. Say something to start the conversation.
          </p>
        ) : null}
        {saved.map((message) => (
          <MessageBubble key={message.id} {...message} />
        ))}
        {draft && (
          <>
            <MessageBubble {...draft.user} />
            <MessageBubble
              role="assistant"
              content={draft.content}
              toolCalls={draft.toolCalls}
              streaming
            />
          </>
        )}
      </div>

      <div className="space-y-2 border-t border-border bg-surface p-3">
        {wrongAnswersUsedUp && (
          <p role="alert" className="text-sm font-medium text-bad">
            You&apos;ve used all your wrong answers for this bot, so its chat is closed.
            You can still read your conversations, and other bots aren&apos;t affected.
          </p>
        )}
        {rateLimitSeconds > 0 && (
          <p role="status" className="text-sm font-medium text-warn">
            You&apos;re sending messages too quickly. Try again in {rateLimitSeconds}s.
          </p>
        )}
        {submitted && (
          <p className="text-sm text-muted">
            This conversation was submitted and can&apos;t be continued. Start a new one to keep trying.
          </p>
        )}
        {streamFailure && (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-bad p-2.5 text-sm"
          >
            {streamFailure === "budget" ? (
              <p>
                <span className="font-medium text-bad">Your team has insufficient AI budget.</span>{" "}
                Your budget is exhausted or the remaining amount is too small for this request.
                Contact the organisers to increase your allowance.
              </p>
            ) : streamFailure === "bot" ? (
              <p>
                <span className="font-medium text-bad">The bot couldn&apos;t finish that reply.</span>{" "}
                Your message is back in the box. Provider work already completed may still count toward your budget.
              </p>
            ) : (
              <p>
                <span className="font-medium text-bad">The connection dropped.</span>{" "}
                Check your conversation history before retrying.
              </p>
            )}
            <Button variant="ghost" onClick={clearError}>Dismiss</Button>
          </div>
        )}
        {showGenericError && (
          <div className="flex items-center justify-between gap-2">
            <InlineError error={error} />
            <Button variant="ghost" onClick={clearError}>Dismiss</Button>
          </div>
        )}
        <form onSubmit={submit} className="flex items-end gap-2">
          <Textarea
            aria-label="Message"
            rows={2}
            placeholder={disabled ? "Messaging unavailable" : "Type a message. Enter to send, Shift+Enter for a new line."}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={wrongAnswersUsedUp || submitted}
            className="resize-none"
          />
          <Button type="submit" aria-label="Send message" disabled={disabled || !input.trim()}>
            <Send className="size-4" aria-hidden />
          </Button>
        </form>
      </div>
    </div>
  );
}
