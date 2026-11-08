import "server-only";
import type { AdminApi, AuthApi, TeamApi } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { MODELS } from "@/lib/constants";
import type {
  AdminBot,
  AdminConversationSummary,
  AdminLevelCompletion,
  AdminJudging,
  AdminModel,
  AdminPrizes,
  AdminSubmission,
  AdminTeam,
  BotCard,
  BotDetail,
  BotInput,
  BotStatus,
  ChatEvent,
  ChatMessage,
  ConversationSummary,
  LeaderboardEntry,
  LevelProgress,
  ModelName,
  Paginated,
  Session,
  Submission,
  TeamProfile,
  SubmissionStatus,
} from "@/lib/types";
import {
  buildReply,
  estimateTokens,
  REASON_ANSWER_CORRECT,
  REASON_ANSWER_INCORRECT,
  REASON_FAILED,
  REASON_PASSED,
} from "./content";
import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  getStore,
  type ConversationRow,
  type SubmissionRow,
  type TeamRow,
} from "./store";

const RATE_LIMIT_MESSAGES = 6;
const RATE_LIMIT_WINDOW_MS = 30_000;
const PENDING_MS = 3_000;
const GRADING_MS = 6_000;
const STREAM_START_MS = 300;
const STREAM_TOOL_MS = 400;
const STREAM_WORD_MS = 35;
/** Saying this in a live mock conversation makes the bot "break", for demos. */
const BREAK_PHRASE = "open sesame";
/** Saying this makes the reply fail partway, for demos. */
const STREAM_FAILURE_PHRASE = "simulate failure";
const STREAM_ERROR_RATE = Number(process.env.MOCK_ERROR_RATE ?? 0.02);

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function newId(prefix: string): string {
  return `${prefix}-${getStore().nextId++}`;
}

// ---------- Lookups and derived values ----------

function botRow(id: string): AdminBot {
  const bot = getStore().bots.find((b) => b.id === id);
  if (!bot) throw new ApiError("not_found", "Bot not found.");
  return bot;
}

function teamRow(id: string) {
  const team = getStore().teams.find((t) => t.id === id);
  if (!team) throw new ApiError("not_found", "Team not found.");
  return team;
}

function byLevelThenLabel<T extends { level: number; label: string }>(a: T, b: T) {
  return a.level - b.level || a.label.localeCompare(b.label);
}

/** Moves in-flight submissions along. Called at the start of every request. */
export function advanceSubmissions() {
  const store = getStore();
  // Off: everything waits, as the real worker claims nothing. Back on, they're graded in order.
  if (!store.judgingEnabled) return;
  const now = Date.now();
  for (const submission of store.submissions) {
    if (submission.status === "pending" && now >= submission.gradingAt) {
      submission.status = "grading";
    }
    if (submission.status === "grading" && now >= submission.finishAt) {
      const conversation = store.conversations.find(
        (c) => c.id === submission.conversationId,
      )!;
      const alreadySolved = hasOtherPass(submission);
      submission.status = conversation.broken ? "passed" : "failed";
      submission.reason = conversation.broken ? REASON_PASSED : REASON_FAILED;
      submission.pointsAwarded =
        conversation.broken && !alreadySolved ? botRow(submission.botId).points : 0;
      submission.gradedAt = new Date(now).toISOString();
    }
  }
}

function hasOtherPass(submission: Pick<SubmissionRow, "teamId" | "botId">, except?: SubmissionRow) {
  return getStore().submissions.some(
    (s) =>
      s !== submission &&
      s !== except &&
      s.teamId === submission.teamId &&
      s.botId === submission.botId &&
      s.status === "passed",
  );
}

function wrongAnswersUsed(teamId: string, botId: string): number {
  return getStore().submissions.filter(
    (s) =>
      s.teamId === teamId &&
      s.botId === botId &&
      s.conversationId === null &&
      s.status === "failed",
  ).length;
}

function solvedIds(teamId: string): Set<string> {
  return new Set(
    getStore()
      .submissions.filter((s) => s.teamId === teamId && s.status === "passed")
      .map((s) => s.botId),
  );
}

function attemptedIds(teamId: string): Set<string> {
  return new Set(
    getStore()
      .conversations.filter((c) => c.teamId === teamId)
      .map((c) => c.botId),
  );
}

function scoreOf(teamId: string): number {
  return getStore()
    .submissions.filter((s) => s.teamId === teamId && s.status === "passed")
    .reduce((sum, s) => sum + (s.pointsAwarded ?? 0), 0);
}

function levelConfig(level: number) {
  const config = getStore().levels.find((l) => l.level === level);
  if (!config) throw new ApiError("not_found", "Level not found.");
  return config;
}

/** Completion of one level for one team, before any prize limit is applied. */
function levelState(teamId: string, level: number) {
  const store = getStore();
  const config = levelConfig(level);
  const bots = store.bots.filter((b) => b.level === level && b.enabled);
  const botIds = new Set(bots.map((b) => b.id));
  const firstPass = new Map<string, string>();
  for (const s of store.submissions) {
    if (s.teamId !== teamId || s.status !== "passed" || !botIds.has(s.botId)) continue;
    const at = s.gradedAt ?? s.createdAt;
    const known = firstPass.get(s.botId);
    if (!known || at < known) firstPass.set(s.botId, at);
  }
  const passTimes = [...firstPass.values()].sort();
  const required = Math.min(config.requiredBots, bots.length);
  const earned = bots.length > 0 && passTimes.length >= required;
  const entry = store.overrides.get(`${teamId}:${level}`);
  const override = entry?.override ?? null;
  const complete = override === "revoked" ? false : override === "granted" || earned;
  const completedAt = !complete
    ? null
    : override === "granted"
      ? entry!.at
      : passTimes[required - 1];
  return { solved: passTimes.length, required, total: bots.length, earned, override, complete, completedAt };
}

function prizeFor(teamId: string, level: number): number {
  const store = getStore();
  const config = levelConfig(level);
  const state = levelState(teamId, level);
  if (!state.complete) return 0;
  if (config.maxWinners === null) return config.prizeAmount;
  const earlier = store.teams.filter((t) => {
    if (t.id === teamId) return false;
    const other = levelState(t.id, level);
    return other.complete && other.completedAt! < state.completedAt!;
  }).length;
  return earlier < config.maxWinners ? config.prizeAmount : 0;
}

function isLevelLocked(teamId: string, level: number): boolean {
  const store = getStore();
  if (!store.requireLevelsInOrder) return false;
  return store.levels.some((l) => {
    if (l.level >= level) return false;
    const state = levelState(teamId, l.level);
    return state.total > 0 && !state.complete;
  });
}

function statusOf(
  bot: Pick<AdminBot, "id" | "level">,
  teamId: string,
  solved: Set<string>,
  attempted: Set<string>,
): BotStatus {
  if (solved.has(bot.id)) return "solved";
  if (isLevelLocked(teamId, bot.level)) return "locked";
  return attempted.has(bot.id) ? "attempted" : "unattempted";
}

function ranking(): { team: TeamRow; score: number; solved: number }[] {
  const store = getStore();
  const lastPass = (teamId: string) =>
    store.submissions
      .filter((s) => s.teamId === teamId && s.status === "passed")
      .reduce((max, s) => (s.createdAt > max ? s.createdAt : max), "");
  return store.teams
    .filter((t) => !t.disabled)
    .map((team) => ({
      team,
      score: scoreOf(team.id),
      solved: solvedIds(team.id).size,
      last: lastPass(team.id),
    }))
    .sort(
      (a, b) =>
        b.score - a.score ||
        a.last.localeCompare(b.last) ||
        a.team.name.localeCompare(b.team.name),
    );
}

function rankOf(teamId: string): number | null {
  const index = ranking().findIndex((r) => r.team.id === teamId);
  return index < 0 ? null : index + 1;
}

function tokensIn(conversation: ConversationRow): number {
  return conversation.messages.reduce((sum, m) => sum + m.tokens, 0);
}

function tokensUsedBy(teamId: string): number {
  return getStore()
    .conversations.filter((c) => c.teamId === teamId)
    .reduce((sum, c) => sum + tokensIn(c), 0);
}

const MOCK_BUDGET_USD = 5;
const MOCK_USD_PER_TOKEN = 0.00001;

function modelTokensUsed(teamId: string, model: string): number {
  return getStore()
    .conversations.filter((c) => c.teamId === teamId && c.model === model)
    .reduce((sum, c) => sum + tokensIn(c), 0);
}


// ---------- Mappers ----------
// Fields are copied one by one so admin-only data cannot reach a team by accident.

function toProfile(team: TeamRow): TeamProfile {
  return {
    id: team.id,
    name: team.name,
    tokensUsed: tokensUsedBy(team.id),
  };
}

function toCard(bot: AdminBot, status: BotStatus): BotCard {
  return {
    id: bot.id,
    level: bot.level,
    label: bot.label,
    model: bot.model,
    checkType: bot.checkType,
    points: bot.points,
    status,
  };
}

function toMessage(message: ChatMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    toolCalls: message.toolCalls,
    tokens: message.tokens,
    createdAt: message.createdAt,
  };
}

// Teams see anything not yet graded as grading, so "pending" can't reveal that
// organisers have switched judging off.
function teamStatus<T extends SubmissionStatus | null>(status: T): T {
  return (status === "pending" ? "grading" : status) as T;
}

function toSubmission(row: SubmissionRow): Submission {
  const bot = botRow(row.botId);
  return {
    id: row.id,
    conversationId: row.conversationId,
    botId: row.botId,
    level: bot.level,
    botLabel: bot.label,
    checkType: bot.checkType,
    status: teamStatus(row.status),
    reason: row.reason,
    pointsAwarded: row.pointsAwarded,
    createdAt: row.createdAt,
    gradedAt: row.gradedAt,
  };
}

function submissionFor(conversationId: string) {
  return getStore().submissions.find((s) => s.conversationId === conversationId);
}

function toConversationSummary(row: ConversationRow): ConversationSummary {
  const first = row.messages.find((m) => m.role === "user")?.content ?? "New conversation";
  return {
    id: row.id,
    botId: row.botId,
    title: first.length > 60 ? `${first.slice(0, 57)}...` : first,
    messageCount: row.messages.length,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    submissionStatus: teamStatus(submissionFor(row.id)?.status ?? null),
  };
}

function toAdminTeam(team: TeamRow): AdminTeam {
  const activity = getStore()
    .conversations.filter((c) => c.teamId === team.id)
    .map((c) => c.updatedAt)
    .sort();
  return {
    id: team.id,
    name: team.name,
    disabled: team.disabled,
    tokensUsed: tokensUsedBy(team.id),
    attempted: attemptedIds(team.id).size,
    solved: solvedIds(team.id).size,
    score: scoreOf(team.id),
    rank: rankOf(team.id),
    lastActiveAt: activity[activity.length - 1] ?? null,
    createdAt: team.createdAt,
  };
}

function toAdminSubmission(row: SubmissionRow): AdminSubmission {
  return {
    ...toSubmission(row),
    status: row.status,
    teamId: row.teamId,
    teamName: teamRow(row.teamId).name,
    model: botRow(row.botId).model,
  };
}

function toAdminConversation(row: ConversationRow): AdminConversationSummary {
  const summary = toConversationSummary(row);
  const bot = botRow(row.botId);
  return {
    ...summary,
    submissionStatus: submissionFor(row.id)?.status ?? null,
    teamId: row.teamId,
    teamName: teamRow(row.teamId).name,
    botLabel: bot.label,
    level: bot.level,
    model: row.model,
    tokens: tokensIn(row),
    result:
      bot.checkType === "answer" ? "answer_bot" : (submissionFor(row.id)?.status ?? "not_submitted"),
  };
}

function toJudging(): AdminJudging {
  const store = getStore();
  return {
    enabled: store.judgingEnabled,
    pausedUntil: null,
    updatedAt: store.judgingUpdatedAt,
    waiting: store.submissions.filter((s) => s.status === "pending" || s.status === "grading").length,
    needsOrganiser: 0,
  };
}

function toAdminModel(model: ModelName): AdminModel {
  return { model };
}

function toAdminPrizes(): AdminPrizes {
  const store = getStore();
  const teams = store.teams.map((team) => {
    const levels = store.levels.map((config): AdminLevelCompletion => {
      const state = levelState(team.id, config.level);
      return {
        level: config.level,
        solved: state.solved,
        required: state.required,
        total: state.total,
        earned: state.earned,
        override: state.override,
        complete: state.complete,
        prizeAwarded: prizeFor(team.id, config.level),
        completedAt: state.completedAt,
      };
    });
    return {
      teamId: team.id,
      teamName: team.name,
      levels,
      prizeOwed: levels.reduce((sum, l) => sum + l.prizeAwarded, 0),
    };
  });
  return {
    requireLevelsInOrder: store.requireLevelsInOrder,
    levels: store.levels,
    teams,
    totalPrizeOwed: teams.reduce((sum, t) => sum + t.prizeOwed, 0),
  };
}

function paginate<T>(items: T[], page = 1, pageSize = 20): Paginated<T> {
  return {
    items: items.slice((page - 1) * pageSize, page * pageSize),
    total: items.length,
    page,
    pageSize,
  };
}

function newestFirst<T extends { createdAt: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

function validateBot(input: BotInput) {
  if (!input.label?.trim() || !input.systemPrompt?.trim()) {
    throw new ApiError("validation", "Label and system prompt are required.");
  }
  if (input.checkType === "answer" && !input.secret?.trim()) {
    throw new ApiError("validation", "A password is required for an answer bot.");
  }
  if (input.checkType === "judge" && !input.winRubric?.trim()) {
    throw new ApiError("validation", "A win rubric is required for a judge bot.");
  }
  if (!(input.points > 0)) {
    throw new ApiError("validation", "Points must be greater than zero.");
  }
  levelConfig(input.level);
}

// ---------- Handlers ----------

export const authHandlers: Pick<AuthApi, "login" | "adminLogin"> = {
  async login({ team, password }) {
    const needle = team?.trim().toLowerCase();
    const row = getStore().teams.find(
      (t) => t.id.toLowerCase() === needle || t.name.toLowerCase() === needle,
    );
    if (!row || row.password !== password) {
      throw new ApiError("unauthorized", "Incorrect team or password.");
    }
    if (row.disabled) {
      throw new ApiError("forbidden", "This team has been disabled. Please contact the organisers.");
    }
    return { role: "team", teamId: row.id, teamName: row.name };
  },
  async adminLogin({ username, password }) {
    if (username !== ADMIN_USERNAME || password !== ADMIN_PASSWORD) {
      throw new ApiError("unauthorized", "Incorrect username or password.");
    }
    return { role: "admin", username };
  },
};

type WithTeam<T> = {
  [K in keyof T]: T[K] extends (...args: infer A) => infer R
    ? (teamId: string, ...args: A) => R
    : never;
};

function ownConversation(teamId: string, id: string): ConversationRow {
  const row = getStore().conversations.find((c) => c.id === id && c.teamId === teamId);
  if (!row) throw new ApiError("not_found", "Conversation not found.");
  return row;
}

function visibleBot(teamId: string, id: string) {
  const bot = getStore().bots.find((b) => b.id === id && b.enabled);
  if (!bot) throw new ApiError("not_found", "Bot not found.");
  const status = statusOf(bot, teamId, solvedIds(teamId), attemptedIds(teamId));
  if (status === "locked") {
    throw new ApiError("forbidden", "Complete the previous level to unlock this one.");
  }
  return { bot, status };
}

export const teamHandlers: WithTeam<TeamApi> = {
  async getMe(teamId) {
    return toProfile(teamRow(teamId));
  },

  async getBudget(teamId) {
    const spentUsd = Math.min(tokensUsedBy(teamId) * MOCK_USD_PER_TOKEN, MOCK_BUDGET_USD);
    return { budgetUsd: MOCK_BUDGET_USD, spentUsd, remainingUsd: MOCK_BUDGET_USD - spentUsd };
  },

  async getDashboard(teamId) {
    const store = getStore();
    const enabled = store.bots.filter((b) => b.enabled);
    const solved = solvedIds(teamId);
    const attempted = attemptedIds(teamId);
    return {
      points: scoreOf(teamId),
      rank: rankOf(teamId),
      totalTeams: ranking().length,
      attempted: enabled.filter((b) => attempted.has(b.id)).length,
      solved: enabled.filter((b) => solved.has(b.id)).length,
      remaining: enabled.filter((b) => !solved.has(b.id)).length,
      levelsCompleted: store.levels.filter((l) => levelState(teamId, l.level).complete).length,
      totalLevels: store.levels.length,
      prizeEarned: store.levels.reduce((sum, l) => sum + prizeFor(teamId, l.level), 0),
      usageByModel: MODELS.map((model) => {
        const tokensUsed = modelTokensUsed(teamId, model);
        return { model, tokensUsed };
      }),
    };
  },

  async getLevels(teamId) {
    const store = getStore();
    const solved = solvedIds(teamId);
    const attempted = attemptedIds(teamId);
    return store.levels.map((config): LevelProgress => {
      const state = levelState(teamId, config.level);
      return {
        level: config.level,
        name: config.name,
        locked: isLevelLocked(teamId, config.level),
        solved: state.solved,
        required: state.required,
        total: state.total,
        complete: state.complete,
        revoked: state.override === "revoked",
        prizeAmount: config.prizeAmount,
        prizeAwarded: prizeFor(teamId, config.level),
        bots: store.bots
          .filter((b) => b.level === config.level && b.enabled)
          .sort((a, b) => MODELS.indexOf(a.model) - MODELS.indexOf(b.model) || a.label.localeCompare(b.label))
          .map((b) => toCard(b, statusOf(b, teamId, solved, attempted))),
      };
    });
  },

  async getBot(teamId, id) {
    const { bot, status } = visibleBot(teamId, id);
    const detail: BotDetail = {
      ...toCard(bot, status),
      levelName: levelConfig(bot.level).name,
      description: bot.description,
      goal: bot.goal,
      wrongAnswersUsed: wrongAnswersUsed(teamId, bot.id),
      maxWrongAnswers: getStore().maxWrongAnswers,
    };
    return detail;
  },

  async getConversations(teamId, botId) {
    return newestFirst(
      getStore().conversations.filter((c) => c.teamId === teamId && c.botId === botId),
    ).map(toConversationSummary);
  },

  async getConversation(teamId, id) {
    const row = ownConversation(teamId, id);
    const submission = submissionFor(row.id);
    return {
      ...toConversationSummary(row),
      messages: row.messages.map(toMessage),
      submission: submission ? toSubmission(submission) : null,
    };
  },

  async sendMessage(teamId, { botId, conversationId, content }) {
    const store = getStore();
    const text = content?.trim();
    if (!text) throw new ApiError("validation", "Message cannot be empty.");
    const { bot } = visibleBot(teamId, botId);

    if (bot.checkType === "answer" && wrongAnswersUsed(teamId, botId) >= store.maxWrongAnswers) {
      throw new ApiError("conflict", "You've used all your wrong answers for this bot, so its chat is closed.");
    }


    const now = Date.now();
    const recent = (store.messageTimes.get(teamId) ?? []).filter(
      (time) => now - time < RATE_LIMIT_WINDOW_MS,
    );
    if (recent.length >= RATE_LIMIT_MESSAGES) {
      throw new ApiError(
        "rate_limited",
        "You're sending messages too quickly.",
        Math.ceil((recent[0] + RATE_LIMIT_WINDOW_MS - now) / 1000),
      );
    }

    const lowered = text.toLowerCase();
    const willFail = lowered.includes(STREAM_FAILURE_PHRASE) || Math.random() < STREAM_ERROR_RATE;

    let conversation: ConversationRow;
    if (conversationId) {
      conversation = ownConversation(teamId, conversationId);
      if (conversation.botId !== botId) {
        throw new ApiError("not_found", "Conversation not found.");
      }
      if (submissionFor(conversation.id)) {
        throw new ApiError("conflict", "This conversation was submitted and can't be continued.");
      }
    } else {
      conversation = {
        id: newId("conv"),
        teamId,
        botId,
        model: bot.model,
        messages: [],
        broken: false,
        createdAt: new Date(now).toISOString(),
        updatedAt: new Date(now).toISOString(),
      };
      // A failed first turn leaves nothing behind.
      if (!willFail) store.conversations.push(conversation);
    }
    if (!willFail) store.messageTimes.set(teamId, [...recent, now]);

    const brokeNow = lowered.includes(BREAK_PHRASE);
    if (!willFail) conversation.broken ||= brokeNow;
    const reply = buildReply(
      bot,
      brokeNow,
      (items) => items[Math.floor(Math.random() * items.length)],
    );
    const timestamp = new Date().toISOString();
    const userMessage: ChatMessage = {
      id: newId("m"),
      role: "user",
      content: text,
      toolCalls: [],
      tokens: estimateTokens(text),
      createdAt: timestamp,
    };
    const assistantMessage: ChatMessage = {
      id: newId("m"),
      role: "assistant",
      content: reply.content,
      toolCalls: reply.toolCalls,
      tokens: estimateTokens(reply.content) + 60 + Math.floor(Math.random() * 120),
      createdAt: timestamp,
    };
    // Saved before streaming, so a dropped connection still charges the team.
    // A failed turn is neither saved nor charged, and doesn't count towards the rate limit.
    if (!willFail) {
      conversation.messages.push(userMessage, assistantMessage);
      conversation.updatedAt = timestamp;
    }

    return (async function* (): AsyncGenerator<ChatEvent> {
      yield {
        type: "start",
        conversationId: conversation.id,
        userMessage: toMessage(userMessage),
        assistantMessageId: assistantMessage.id,
      };
      await sleep(STREAM_START_MS);
      for (const toolCall of reply.toolCalls) {
        yield { type: "tool_call", toolCall };
        await sleep(STREAM_TOOL_MS);
      }
      const words = reply.content.split(/(?<=\s)/);
      for (const word of willFail ? words.slice(0, Math.ceil(words.length / 2)) : words) {
        yield { type: "delta", text: word };
        await sleep(STREAM_WORD_MS);
      }
      yield willFail
        ? { type: "error", code: "bot_unavailable" }
        : { type: "done", message: toMessage(assistantMessage) };
    })();
  },

  async submitConversation(teamId, conversationId) {
    const store = getStore();
    const conversation = ownConversation(teamId, conversationId);
    const { bot } = visibleBot(teamId, conversation.botId);
    if (bot.checkType !== "judge") {
      throw new ApiError("conflict", "This bot is checked with the answer box.");
    }
    if (conversation.messages.length === 0) {
      throw new ApiError("validation", "This conversation has no messages.");
    }
    if (submissionFor(conversation.id)) {
      throw new ApiError("conflict", "This conversation was already submitted.");
    }
    const now = Date.now();
    const row: SubmissionRow = {
      id: newId("sub"),
      teamId,
      conversationId,
      botId: conversation.botId,
      status: "pending",
      reason: null,
      pointsAwarded: null,
      createdAt: new Date(now).toISOString(),
      gradedAt: null,
      gradingAt: now + PENDING_MS,
      finishAt: now + PENDING_MS + GRADING_MS,
    };
    store.submissions.push(row);
    return toSubmission(row);
  },

  async submitAnswer(teamId, botId, answer) {
    const { bot } = visibleBot(teamId, botId);
    if (bot.checkType !== "answer") {
      throw new ApiError("conflict", "This bot is graded by the judge.");
    }
    const guess = answer?.trim();
    if (!guess) throw new ApiError("validation", "Enter a password.");
    if (wrongAnswersUsed(teamId, botId) >= getStore().maxWrongAnswers) {
      throw new ApiError("conflict", "You've used all your wrong answers for this bot.");
    }
    const correct = guess === bot.secret;
    const pointsAwarded = correct && !hasOtherPass({ teamId, botId }) ? bot.points : 0;
    const timestamp = new Date().toISOString();
    getStore().submissions.push({
      id: newId("sub"),
      teamId,
      conversationId: null,
      botId,
      status: correct ? "passed" : "failed",
      reason: correct ? REASON_ANSWER_CORRECT : REASON_ANSWER_INCORRECT,
      pointsAwarded,
      createdAt: timestamp,
      gradedAt: timestamp,
      gradingAt: 0,
      finishAt: 0,
    });
    return { correct, pointsAwarded };
  },

  async getSubmissions(teamId) {
    return newestFirst(getStore().submissions.filter((s) => s.teamId === teamId)).map(
      toSubmission,
    );
  },

  async getLeaderboard(teamId) {
    return ranking().map(
      (row, i): LeaderboardEntry => ({
        rank: i + 1,
        teamId: row.team.id,
        teamName: row.team.name,
        score: row.score,
        solved: row.solved,
        isOwnTeam: row.team.id === teamId,
      }),
    );
  },
};

export const adminHandlers: AdminApi = {
  async getAdminOverview() {
    const store = getStore();
    const dayMs = 86_400_000;
    const now = Date.now();
    const days = Array.from({ length: 7 }, (_, i) =>
      new Date(now - (6 - i) * dayMs).toISOString().slice(0, 10),
    );
    const series = days.map((date) => ({
      date,
      attempts: 0,
      solves: 0,
      tokensConsumed: 0,
    }));
    const bucket = (iso: string) => series.find((p) => p.date === iso.slice(0, 10));

    for (const conversation of store.conversations) {
      const point = bucket(conversation.createdAt);
      if (point) point.attempts++;
      for (const message of conversation.messages) {
        const messagePoint = bucket(message.createdAt);
        if (messagePoint) {
          messagePoint.tokensConsumed += message.tokens;
        }
      }
    }
    const solves = store.submissions.filter((s) => (s.pointsAwarded ?? 0) > 0);
    for (const submission of solves) {
      const point = bucket(submission.createdAt);
      if (point) point.solves++;
    }

    const activeSince = new Date(now - dayMs).toISOString();
    return {
      totalTeams: store.teams.length,
      activeTeams: new Set(
        store.conversations.filter((c) => c.updatedAt >= activeSince).map((c) => c.teamId),
      ).size,
      tokensConsumed: store.conversations.reduce((sum, c) => sum + tokensIn(c), 0),
      totalAttempts: store.conversations.length,
      totalSolves: solves.length,
      series,
    };
  },

  async getAdminTeams() {
    return getStore().teams.map(toAdminTeam);
  },

  async getAdminTeam(id) {
    const team = teamRow(id);
    const solved = solvedIds(id);
    const attempted = attemptedIds(id);
    const store = getStore();
    return {
      ...toAdminTeam(team),
      progress: [...store.bots].sort(byLevelThenLabel).map((b) => ({
        botId: b.id,
        label: b.label,
        level: b.level,
        model: b.model,
        status: statusOf(b, id, solved, attempted),
        conversations: store.conversations.filter(
          (row) => row.teamId === id && row.botId === b.id,
        ).length,
      })),
    };
  },

  async createTeam({ name, password }) {
    const store = getStore();
    const trimmed = name?.trim();
    if (!trimmed || !password) {
      throw new ApiError("validation", "Team name and password are required.");
    }
    if (store.teams.some((t) => t.name.toLowerCase() === trimmed.toLowerCase())) {
      throw new ApiError("conflict", "A team with this name already exists.");
    }
    const team: TeamRow = {
      id: `t-${String(store.teams.length + 1).padStart(2, "0")}`,
      name: trimmed,
      password,
      disabled: false,
      createdAt: new Date().toISOString(),
    };
    store.teams.push(team);
    return toAdminTeam(team);
  },

  async updateTeam(id, input) {
    const team = teamRow(id);
    if (input.name !== undefined) {
      const trimmed = input.name.trim();
      if (!trimmed) throw new ApiError("validation", "Team name is required.");
      const taken = getStore().teams.some(
        (t) => t.id !== id && t.name.toLowerCase() === trimmed.toLowerCase(),
      );
      if (taken) throw new ApiError("conflict", "A team with this name already exists.");
      team.name = trimmed;
    }
    if (input.disabled !== undefined) team.disabled = input.disabled;
    return toAdminTeam(team);
  },

  async resetTeamPassword(id) {
    const team = teamRow(id);
    team.password = `temp-${crypto.randomUUID().slice(0, 8)}`;
    return { temporaryPassword: team.password };
  },

  async getAdminConversations({ teamId, botId, model, result, page, pageSize }) {
    const rows = newestFirst(getStore().conversations)
      .map(toAdminConversation)
      .filter(
        (c) =>
          (!teamId || c.teamId === teamId) &&
          (!botId || c.botId === botId) &&
          (!model || c.model === model) &&
          (!result || c.result === result),
      );
    return paginate(rows, page, pageSize);
  },

  async getAdminConversation(id) {
    const row = getStore().conversations.find((c) => c.id === id);
    if (!row) throw new ApiError("not_found", "Conversation not found.");
    const submission = submissionFor(id);
    return {
      ...toAdminConversation(row),
      // The mock keeps no failed turns.
      messages: row.messages.map((m) => ({ ...toMessage(m), status: "complete" as const })),
      verdict: submission ? toAdminSubmission(submission) : null,
    };
  },

  async getAdminSubmissions({ status, teamId, page, pageSize }) {
    const rows = newestFirst(getStore().submissions)
      .filter((s) => (!status || s.status === status) && (!teamId || s.teamId === teamId))
      .map(toAdminSubmission);
    return paginate(rows, page, pageSize);
  },

  async regradeSubmission(id) {
    const row = getStore().submissions.find((s) => s.id === id);
    if (!row) throw new ApiError("not_found", "Submission not found.");
    if (botRow(row.botId).checkType !== "judge" || !row.conversationId) {
      throw new ApiError("conflict", "Answer checks aren't graded by the judge.");
    }
    if (row.status === "pending" || row.status === "grading") {
      throw new ApiError("conflict", "This submission is already being graded.");
    }
    const now = Date.now();
    row.status = "pending";
    row.reason = null;
    row.pointsAwarded = null;
    row.gradedAt = null;
    row.gradingAt = now + PENDING_MS;
    row.finishAt = now + PENDING_MS + GRADING_MS;
    return toAdminSubmission(row);
  },

  async getBotStats() {
    const store = getStore();
    return [...store.bots].sort(byLevelThenLabel).map((b) => {
      const conversations = store.conversations.filter((row) => row.botId === b.id);
      const teamsAttempted = new Set(conversations.map((row) => row.teamId));
      const solvers = new Set(
        store.submissions
          .filter((s) => s.botId === b.id && s.status === "passed")
          .map((s) => s.teamId),
      );
      const solverAttempts = conversations.filter((row) => solvers.has(row.teamId)).length;
      return {
        botId: b.id,
        label: b.label,
        level: b.level,
        model: b.model,
        enabled: b.enabled,
        attempts: conversations.length,
        teamsAttempted: teamsAttempted.size,
        solves: solvers.size,
        solveRate: teamsAttempted.size ? solvers.size / teamsAttempted.size : 0,
        avgAttemptsToSolve: solvers.size ? solverAttempts / solvers.size : null,
        tokensUsed: conversations.reduce((sum, row) => sum + tokensIn(row), 0),
      };
    });
  },

  async getAdminBots() {
    return [...getStore().bots].sort(byLevelThenLabel);
  },

  async getAdminBot(id) {
    return botRow(id);
  },

  async createBot(input) {
    validateBot(input);
    const bot = { ...input, id: `bot-${crypto.randomUUID().slice(0, 10)}` };
    getStore().bots.push(bot);
    return bot;
  },

  async updateBot(id, input) {
    validateBot(input);
    const bot = botRow(id);
    Object.assign(bot, input, { id });
    return bot;
  },

  async setBotEnabled(id, enabled) {
    const bot = botRow(id);
    bot.enabled = enabled;
    return bot;
  },

  async getJudging() {
    return toJudging();
  },

  async setJudging(enabled) {
    if (typeof enabled !== "boolean") throw new ApiError("validation", "Invalid input.");
    const store = getStore();
    if (store.judgingEnabled !== enabled) {
      store.judgingEnabled = enabled;
      store.judgingUpdatedAt = new Date().toISOString();
    }
    return toJudging();
  },

  async getAdminModels() {
    return MODELS.map(toAdminModel);
  },


  async getAdminPrizes() {
    return toAdminPrizes();
  },

  async setLevelOverride(teamId, level, override) {
    teamRow(teamId);
    levelConfig(level);
    const key = `${teamId}:${level}`;
    const { overrides } = getStore();
    if (override === null) {
      overrides.delete(key);
    } else if (override === "granted" || override === "revoked") {
      overrides.set(key, { override, at: new Date().toISOString() });
    } else {
      throw new ApiError("validation", "Unknown override.");
    }
    return toAdminPrizes();
  },
};

export function isActiveTeam(session: Session): boolean {
  if (session.role !== "team") return false;
  const team = getStore().teams.find((t) => t.id === session.teamId);
  return Boolean(team && !team.disabled);
}
