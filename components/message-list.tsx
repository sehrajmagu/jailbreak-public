import { Wrench } from "lucide-react";
import { cn, formatNumber } from "@/lib/format";
import type { ChatMessage, ToolCall } from "@/lib/types";

function prettyJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function ToolCallView({ call }: { call: ToolCall }) {
  return (
    <details className="rounded-md border border-border bg-subtle text-xs">
      <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5 font-medium">
        <Wrench className="size-3.5 shrink-0" aria-hidden />
        <span>
          Tool call: <code className="font-mono">{call.name}</code>
        </span>
      </summary>
      <div className="space-y-2 border-t border-border p-2.5">
        <div>
          <p className="mb-1 text-muted">Arguments</p>
          <pre className="overflow-x-auto font-mono whitespace-pre-wrap">{prettyJson(call.arguments)}</pre>
        </div>
        <div>
          <p className="mb-1 text-muted">Result</p>
          <pre className="overflow-x-auto font-mono whitespace-pre-wrap">{prettyJson(call.result)}</pre>
        </div>
      </div>
    </details>
  );
}

export function MessageBubble({
  role,
  content,
  toolCalls,
  tokens,
  streaming,
  failed,
}: Pick<ChatMessage, "role" | "content" | "toolCalls"> & {
  tokens?: number;
  streaming?: boolean;
  /** A failed turn, shown only in the admin log. */
  failed?: boolean;
}) {
  const isUser = role === "user";
  return (
    <div className={cn("flex", isUser && "justify-end")}>
      <div className="max-w-[85%] min-w-0 space-y-1.5">
        {toolCalls.map((call) => (
          <ToolCallView key={call.id} call={call} />
        ))}
        {(content || streaming) && (
          <div
            className={cn(
              "rounded-lg px-3 py-2 text-sm break-words whitespace-pre-wrap",
              failed
                ? "border border-dashed border-border bg-subtle text-muted"
                : isUser
                  ? "bg-primary text-primary-foreground"
                  : "border border-border bg-surface",
            )}
          >
            <span className="sr-only">{isUser ? "You said: " : "Assistant said: "}</span>
            {content}
            {streaming && (
              <span aria-hidden className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-muted align-middle" />
            )}
          </div>
        )}
        {(failed || (tokens !== undefined && tokens > 0)) && (
          <p className={cn("text-xs text-muted", isUser && "text-right")}>
            {[failed && "Failed turn", tokens ? `${formatNumber(tokens)} tokens` : null]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
      </div>
    </div>
  );
}
