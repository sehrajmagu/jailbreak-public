"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  ConversationFilters,
  CreateTeamInput,
  LevelOverride,
  SubmissionFilters,
  UpdateTeamInput,
} from "@/lib/types";

const POLL_MS = 2_000;

const keys = {
  all: ["admin"],
  overview: ["admin", "overview"],
  teams: ["admin", "teams"],
  team: (id: string) => ["admin", "teams", id],
  conversations: (filters: ConversationFilters) => ["admin", "conversations", filters],
  conversation: (id: string) => ["admin", "conversation", id],
  submissions: (filters: SubmissionFilters) => ["admin", "submissions", filters],
  stats: ["admin", "stats"],
  bots: ["admin", "bots"],
  bot: (id: string) => ["admin", "bots", id],
  models: ["admin", "models"],
  prizes: ["admin", "prizes"],
  judging: ["admin", "judging"],
};

/** Admin mutations touch many derived figures, so refresh everything admin. */
function useAdminMutation<A, R>(mutationFn: (args: A) => Promise<R>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => client.invalidateQueries({ queryKey: keys.all }),
  });
}

export const useAdminOverview = () =>
  useQuery({ queryKey: keys.overview, queryFn: () => api.getAdminOverview() });

export const useAdminTeams = () =>
  useQuery({ queryKey: keys.teams, queryFn: () => api.getAdminTeams() });

export const useAdminTeam = (id: string) =>
  useQuery({ queryKey: keys.team(id), queryFn: () => api.getAdminTeam(id) });

export const useCreateTeam = () =>
  useAdminMutation((input: CreateTeamInput) => api.createTeam(input));

export const useUpdateTeam = (id: string) =>
  useAdminMutation((input: UpdateTeamInput) => api.updateTeam(id, input));

export const useResetTeamPassword = (id: string) =>
  useAdminMutation((_: void) => api.resetTeamPassword(id));

export const useAdminConversations = (filters: ConversationFilters) =>
  useQuery({
    queryKey: keys.conversations(filters),
    queryFn: () => api.getAdminConversations(filters),
    placeholderData: keepPreviousData,
  });

export const useAdminConversation = (id: string) =>
  useQuery({
    queryKey: keys.conversation(id),
    queryFn: () => api.getAdminConversation(id),
    refetchInterval: (q) =>
      q.state.data?.result === "pending" || q.state.data?.result === "grading"
        ? POLL_MS
        : false,
  });

export const useAdminSubmissions = (filters: SubmissionFilters) =>
  useQuery({
    queryKey: keys.submissions(filters),
    queryFn: () => api.getAdminSubmissions(filters),
    placeholderData: keepPreviousData,
    refetchInterval: (q) =>
      q.state.data?.items.some((s) => s.status === "pending" || s.status === "grading")
        ? POLL_MS
        : false,
  });

export const useRegradeSubmission = () =>
  useAdminMutation((id: string) => api.regradeSubmission(id));

export const useJudging = () =>
  useQuery({ queryKey: keys.judging, queryFn: () => api.getJudging(), refetchInterval: 5_000 });

export const useSetJudging = () =>
  useAdminMutation((enabled: boolean) => api.setJudging(enabled));

export const useBotStats = () =>
  useQuery({ queryKey: keys.stats, queryFn: () => api.getBotStats() });

export const useAdminBots = () =>
  useQuery({ queryKey: keys.bots, queryFn: () => api.getAdminBots() });

export const useAdminBot = (id: string) =>
  useQuery({ queryKey: keys.bot(id), queryFn: () => api.getAdminBot(id) });

export const useSetBotEnabled = () =>
  useAdminMutation(({ id, enabled }: { id: string; enabled: boolean }) =>
    api.setBotEnabled(id, enabled),
  );

export const useAdminModels = () =>
  useQuery({ queryKey: keys.models, queryFn: () => api.getAdminModels() });


export const useAdminPrizes = () =>
  useQuery({ queryKey: keys.prizes, queryFn: () => api.getAdminPrizes() });

export const useSetLevelOverride = () =>
  useAdminMutation(
    ({ teamId, level, override }: { teamId: string; level: number; override: LevelOverride | null }) =>
      api.setLevelOverride(teamId, level, override),
  );
