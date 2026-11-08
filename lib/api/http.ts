import type { ApiClient } from "@/lib/api/client";
import { readNdjson, toApiError } from "@/lib/api/errors";
import type { ChatEvent } from "@/lib/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api";

async function send(method: string, path: string, body?: unknown) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    credentials: "include",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await toApiError(res);
  return res;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await send(method, path, body);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

function query(params: object): string {
  const entries = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([key, value]) => [key, String(value)]);
  return entries.length ? `?${new URLSearchParams(entries)}` : "";
}

const seg = encodeURIComponent;

export const httpApi: ApiClient = {
  login: (credentials) => json("POST", "/auth/login", credentials),
  adminLogin: (credentials) => json("POST", "/auth/admin/login", credentials),
  logout: () => json("POST", "/auth/logout"),
  getSession: () => json("GET", "/auth/session"),

  getMe: () => json("GET", "/me"),
  getBudget: () => json("GET", "/me/budget"),
  getDashboard: () => json("GET", "/me/dashboard"),
  getLevels: () => json("GET", "/levels"),
  getBot: (id) => json("GET", `/bots/${seg(id)}`),
  getConversations: (botId) => json("GET", `/bots/${seg(botId)}/conversations`),
  getConversation: (id) => json("GET", `/conversations/${seg(id)}`),
  sendMessage: async (request) =>
    readNdjson<ChatEvent>(await send("POST", "/messages", request)),
  submitConversation: (conversationId) =>
    json("POST", `/conversations/${seg(conversationId)}/submit`),
  submitAnswer: (botId, answer) =>
    json("POST", `/bots/${seg(botId)}/answer`, { answer }),
  getSubmissions: () => json("GET", "/submissions"),
  getLeaderboard: () => json("GET", "/leaderboard"),

  getAdminOverview: () => json("GET", "/admin/overview"),
  getAdminTeams: () => json("GET", "/admin/teams"),
  getAdminTeam: (id) => json("GET", `/admin/teams/${seg(id)}`),
  createTeam: (input) => json("POST", "/admin/teams", input),
  updateTeam: (id, input) => json("PATCH", `/admin/teams/${seg(id)}`, input),
  resetTeamPassword: (id) =>
    json("POST", `/admin/teams/${seg(id)}/reset-password`),
  getAdminConversations: (filters) =>
    json("GET", `/admin/conversations${query(filters)}`),
  getAdminConversation: (id) => json("GET", `/admin/conversations/${seg(id)}`),
  getAdminSubmissions: (filters) =>
    json("GET", `/admin/submissions${query(filters)}`),
  regradeSubmission: (id) =>
    json("POST", `/admin/submissions/${seg(id)}/regrade`),
  getJudging: () => json("GET", "/admin/judging"),
  setJudging: (enabled) => json("PUT", "/admin/judging", { enabled }),
  getBotStats: () => json("GET", "/admin/bot-stats"),
  getAdminBots: () => json("GET", "/admin/bots"),
  getAdminBot: (id) => json("GET", `/admin/bots/${seg(id)}`),
  createBot: (input) => json("POST", "/admin/bots", input),
  updateBot: (id, input) => json("PUT", `/admin/bots/${seg(id)}`, input),
  setBotEnabled: (id, enabled) =>
    json("PATCH", `/admin/bots/${seg(id)}`, { enabled }),
  getAdminModels: () => json("GET", "/admin/models"),
  getAdminPrizes: () => json("GET", "/admin/prizes"),
  setLevelOverride: (teamId, level, override) =>
    json("PUT", `/admin/prizes/${seg(teamId)}/levels/${level}`, { override }),
};
