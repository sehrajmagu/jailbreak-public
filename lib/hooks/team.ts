"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { ChatMessage, SubmissionStatus, ToolCall } from "@/lib/types";

const LEADERBOARD_REFRESH_MS = 15_000;
const SUBMISSION_POLL_MS = 2_000;
// LiteLLM can take about 20 seconds to record spend, so a refetch after a turn may be early.
const BUDGET_REFRESH_MS = 30_000;

const keys = {
  me: ["team", "me"],
  budget: ["team", "budget"],
  dashboard: ["team", "dashboard"],
  levels: ["team", "levels"],
  bot: (id: string) => ["team", "bot", id],
  conversations: (botId: string) => ["team", "conversations", botId],
  conversation: (id: string) => ["team", "conversation", id],
  submissions: ["team", "submissions"],
  leaderboard: ["team", "leaderboard"],
};

const inFlight = (status?: SubmissionStatus | null) =>
  status === "pending" || status === "grading";

function invalidateProgress(client: QueryClient) {
  for (const key of [keys.me, keys.dashboard, keys.levels, keys.submissions, keys.leaderboard]) {
    client.invalidateQueries({ queryKey: key });
  }
}

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: () => api.getMe() });
}

export function useBudget() {
  return useQuery({
    queryKey: keys.budget,
    queryFn: () => api.getBudget(),
    refetchInterval: BUDGET_REFRESH_MS,
    retry: false,
  });
}

export function useDashboard() {
  return useQuery({ queryKey: keys.dashboard, queryFn: () => api.getDashboard() });
}

export function useLevels() {
  return useQuery({ queryKey: keys.levels, queryFn: () => api.getLevels() });
}

export function useBot(id: string) {
  return useQuery({ queryKey: keys.bot(id), queryFn: () => api.getBot(id) });
}

export function useConversations(botId: string) {
  return useQuery({
    queryKey: keys.conversations(botId),
    queryFn: () => api.getConversations(botId),
  });
}

export function useConversation(id: string | null) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.conversation(id ?? ""),
    queryFn: () => api.getConversation(id!),
    enabled: Boolean(id),
    refetchInterval: (q) =>
      inFlight(q.state.data?.submission?.status) ? SUBMISSION_POLL_MS : false,
  });

  // Grading finished while this was open: scores and unlocks may have changed.
  const status = query.data?.submission?.status;
  const previous = useRef(status);
  useEffect(() => {
    if (inFlight(previous.current) && !inFlight(status) && query.data) {
      invalidateProgress(client);
      client.invalidateQueries({ queryKey: keys.bot(query.data.botId) });
      client.invalidateQueries({ queryKey: keys.conversations(query.data.botId) });
    }
    previous.current = status;
  }, [status, client, query.data]);

  return query;
}

export function useSubmissions() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.submissions,
    queryFn: () => api.getSubmissions(),
    refetchInterval: (q) =>
      q.state.data?.some((s) => inFlight(s.status)) ? SUBMISSION_POLL_MS : false,
  });

  const anyInFlight = query.data?.some((s) => inFlight(s.status)) ?? false;
  const previous = useRef(anyInFlight);
  useEffect(() => {
    if (previous.current && !anyInFlight) invalidateProgress(client);
    previous.current = anyInFlight;
  }, [anyInFlight, client]);

  return query;
}

export function useSubmitConversation(botId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => api.submitConversation(conversationId),
    onSuccess: (submission) => {
      if (submission.conversationId) {
        client.invalidateQueries({ queryKey: keys.conversation(submission.conversationId) });
      }
      client.invalidateQueries({ queryKey: keys.conversations(botId) });
      client.invalidateQueries({ queryKey: keys.submissions });
    },
  });
}

export function useSubmitAnswer(botId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (answer: string) => api.submitAnswer(botId, answer),
    onSuccess: () => {
      invalidateProgress(client);
      client.invalidateQueries({ queryKey: keys.bot(botId) });
    },
  });
}

export function useLeaderboard() {
  return useQuery({
    queryKey: keys.leaderboard,
    queryFn: () => api.getLeaderboard(),
    refetchInterval: LEADERBOARD_REFRESH_MS,
  });
}

export interface ChatDraft {
  user: ChatMessage;
  assistantId: string | null;
  content: string;
  toolCalls: ToolCall[];
}

/** Sends a message and exposes the reply as it streams in. */
export function useSendMessage(
  botId: string,
  conversationId: string | null,
  /** Called with null if a new conversation's first turn fails, since it isn't saved. */
  onConversationStarted: (id: string | null) => void,
) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<ChatDraft | null>(null);
  const [error, setError] = useState<unknown>(null);
  // "bot": a failed reply; "budget": a gateway spending refusal.
  // Failed replies stay hidden from the team, but provider work may still cost money.
  // "connection": the stream was lost, so the server may have finished the reply.
  const [streamFailure, setStreamFailure] = useState<"bot" | "budget" | "connection" | null>(null);

  const send = useCallback(
    async (content: string) => {
      setError(null);
      setStreamFailure(null);
      const now = new Date().toISOString();
      setDraft({
        user: { id: "local", role: "user", content, toolCalls: [], tokens: 0, createdAt: now },
        assistantId: null,
        content: "",
        toolCalls: [],
      });
      let activeId = conversationId;
      const failStream = (kind: "bot" | "budget" | "connection") => {
        setStreamFailure(kind);
        if (!conversationId && activeId) {
          activeId = null;
          onConversationStarted(null);
        }
      };
      try {
        const events = await api.sendMessage({ botId, conversationId, content });
        let finished = false;
        let replyFailure: "bot" | "budget" | null = null;
        for await (const event of events) {
          if (event.type === "start") {
            activeId = event.conversationId;
            setDraft((d) =>
              d && { ...d, user: event.userMessage, assistantId: event.assistantMessageId },
            );
            if (!conversationId) onConversationStarted(event.conversationId);
          } else if (event.type === "tool_call") {
            setDraft((d) => d && { ...d, toolCalls: [...d.toolCalls, event.toolCall] });
          } else if (event.type === "delta") {
            setDraft((d) => d && { ...d, content: d.content + event.text });
          } else if (event.type === "done") {
            finished = true;
          } else if (event.type === "error") {
            replyFailure = event.code === "budget_exceeded" ? "budget" : "bot";
            break;
          }
        }
        // A stream that closes without "done" failed too.
        if (!finished) failStream(replyFailure ?? "connection");
        return finished;
      } catch (err) {
        // An ApiError was refused before streaming. Anything else is a broken stream.
        if (err instanceof ApiError) setError(err);
        else failStream("connection");
        return false;
      } finally {
        client.invalidateQueries({ queryKey: keys.me });
        client.invalidateQueries({ queryKey: keys.budget });
        client.invalidateQueries({ queryKey: keys.dashboard });
        client.invalidateQueries({ queryKey: keys.levels });
        client.invalidateQueries({ queryKey: keys.bot(botId) });
        client.invalidateQueries({ queryKey: keys.conversations(botId) });
        if (activeId) {
          await client.invalidateQueries({ queryKey: keys.conversation(activeId) });
        }
        setDraft(null);
      }
    },
    [botId, conversationId, client, onConversationStarted],
  );

  return {
    send,
    draft,
    error,
    streamFailure,
    clearError: () => {
      setError(null);
      setStreamFailure(null);
    },
  };
}
