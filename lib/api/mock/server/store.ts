import "server-only";
import { MODELS } from "@/lib/constants";
import type {
  AdminBot,
  ChatMessage,
  LevelConfig,
  LevelOverride,
  ModelName,
  SubmissionStatus,
} from "@/lib/types";
import {
  buildBots,
  buildReply,
  estimateTokens,
  LEVEL_CONFIG,
  MAX_WRONG_ANSWERS,
  promptFor,
  REASON_ANSWER_CORRECT,
  REASON_ANSWER_INCORRECT,
  REASON_FAILED,
  REASON_PASSED,
  REQUIRE_LEVELS_IN_ORDER,
} from "./content";

export interface TeamRow {
  id: string;
  name: string;
  password: string;
  disabled: boolean;
  createdAt: string;
}

export interface ConversationRow {
  id: string;
  teamId: string;
  botId: string;
  model: ModelName;
  messages: ChatMessage[];
  /** Whether the mock judge will pass this conversation. */
  broken: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface SubmissionRow {
  id: string;
  teamId: string;
  conversationId: string | null;
  botId: string;
  status: SubmissionStatus;
  reason: string | null;
  pointsAwarded: number | null;
  createdAt: string;
  gradedAt: string | null;
  gradingAt: number;
  finishAt: number;
}

export interface Store {
  teams: TeamRow[];
  bots: AdminBot[];
  conversations: ConversationRow[];
  submissions: SubmissionRow[];
  levels: LevelConfig[];
  requireLevelsInOrder: boolean;
  maxWrongAnswers: number;
  /** Off: submissions wait, and nothing is graded. */
  judgingEnabled: boolean;
  judgingUpdatedAt: string | null;
  /** Keyed by `${teamId}:${level}`. */
  overrides: Map<string, { override: LevelOverride; at: string }>;
  messageTimes: Map<string, number[]>;
  nextId: number;
}

export const DEMO_PASSWORD = "example-password-demo";
export const ADMIN_USERNAME = "admin";
export const ADMIN_PASSWORD = "example-password-admin";

const TEAM_NAMES = [
  "Prompt Pirates",
  "Token Rangers",
  "Null Pointers",
  "Segfault Society",
  "Byte Me",
  "The Jailbirds",
  "Chai and Code",
  "Stack Overflowers",
  "Gradient Descenders",
  "Root Cause",
  "Kernel Panic",
  "Curious Coders",
];

const HOUR = 3_600_000;

function mulberry32(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seed(): Store {
  const now = Date.now();
  const random = mulberry32(2026);
  const between = (min: number, max: number) => min + random() * (max - min);
  const pick = <T,>(items: T[]) => items[Math.floor(random() * items.length)];

  const store: Store = {
    teams: [],
    bots: buildBots(),
    conversations: [],
    submissions: [],
    levels: LEVEL_CONFIG.map((level) => ({ ...level })),
    requireLevelsInOrder: REQUIRE_LEVELS_IN_ORDER,
    maxWrongAnswers: MAX_WRONG_ANSWERS,
    judgingEnabled: true,
    judgingUpdatedAt: null,
    overrides: new Map(),
    messageTimes: new Map(),
    nextId: 1,
  };
  const newId = (prefix: string) => `${prefix}-${store.nextId++}`;
  const iso = (time: number) => new Date(time).toISOString();

  TEAM_NAMES.forEach((name, i) => {
    const team: TeamRow = {
      id: `t-${String(i + 1).padStart(2, "0")}`,
      name,
      password: DEMO_PASSWORD,
      disabled: false,
      createdAt: iso(now - 7 * 24 * HOUR),
    };
    store.teams.push(team);

    // The first team, used for demos, lands mid-table. The last is always weakest.
    const standing = i === TEAM_NAMES.length - 1 ? i : (i + 5) % (TEAM_NAMES.length - 1);
    const strength = 1 - standing / TEAM_NAMES.length;

    // Teams work through levels in order and stop at the first they can't complete.
    const plan: { bot: AdminBot; solved: boolean; conversations: number }[] = [];
    for (const { level, requiredBots } of store.levels) {
      const solvedCount = Math.min(
        6,
        Math.max(0, Math.round(strength * 6 - (level - 1) * 0.6 + between(-1, 1))),
      );
      const attemptedCount = Math.min(6, solvedCount + (random() < 0.7 ? 2 : 1));
      // Bots with easier models tend to be solved first.
      const ordered = store.bots
        .filter((bot) => bot.level === level)
        .map((bot) => ({ bot, sort: MODELS.indexOf(bot.model) + between(-1.5, 1.5) }))
        .sort((a, b) => a.sort - b.sort);
      ordered.slice(0, attemptedCount).forEach(({ bot }, index) => {
        plan.push({ bot, solved: index < solvedCount, conversations: Math.ceil(between(0.2, 3)) });
      });
      if (solvedCount < requiredBots) break;
    }

    const span = between(30, 140) * HOUR;
    const gap = span / Math.max(1, plan.reduce((sum, p) => sum + p.conversations, 0));
    const latest = now - between(0.5, 3) * HOUR;
    let time = now - span;

    for (const { bot, solved, conversations } of plan) {
      for (let n = 0; n < conversations; n++) {
        time = Math.min(time + gap * between(0.5, 1.5), latest);
        const broken = solved && n === conversations - 1;
        const exchanges = Math.ceil(between(1, 4));
        const messages: ChatMessage[] = [];
        let clock = time;

        for (let turn = 0; turn < exchanges; turn++) {
          const prompt = promptFor(bot.level, turn + n);
          messages.push({
            id: newId("m"),
            role: "user",
            content: prompt,
            toolCalls: [],
            tokens: estimateTokens(prompt),
            createdAt: iso(clock),
          });
          clock += between(10_000, 90_000);
          const reply = buildReply(bot, broken && turn === exchanges - 1, pick);
          messages.push({
            id: newId("m"),
            role: "assistant",
            content: reply.content,
            toolCalls: reply.toolCalls,
            tokens: estimateTokens(reply.content) + Math.round(between(40, 220)),
            createdAt: iso(clock),
          });
          clock += between(20_000, 120_000);
        }

        const conversation: ConversationRow = {
          id: newId("conv"),
          teamId: team.id,
          botId: bot.id,
          model: bot.model,
          messages,
          broken,
          createdAt: iso(time),
          updatedAt: iso(clock),
        };
        store.conversations.push(conversation);

        const isAnswer = bot.checkType === "answer";
        if (broken || random() < (isAnswer ? 0.35 : 0.55)) {
          store.submissions.push({
            id: newId("sub"),
            teamId: team.id,
            conversationId: isAnswer ? null : conversation.id,
            botId: bot.id,
            status: broken ? "passed" : "failed",
            reason: isAnswer
              ? broken ? REASON_ANSWER_CORRECT : REASON_ANSWER_INCORRECT
              : broken ? REASON_PASSED : REASON_FAILED,
            pointsAwarded: broken ? bot.points : 0,
            createdAt: iso(clock),
            gradedAt: iso(clock + (isAnswer ? 0 : 45_000)),
            gradingAt: 0,
            finishAt: 0,
          });
        }
        time = clock;
      }
    }
  });


  // Keep some submissions in flight just after startup, for the admin screens.
  const judgeBots = new Set(store.bots.filter((b) => b.checkType === "judge").map((b) => b.id));
  const inFlight = store.conversations.filter(
    (c) => judgeBots.has(c.botId) && !store.submissions.some((s) => s.conversationId === c.id),
  );
  inFlight.slice(0, 2).forEach((conversation, i) => {
    store.submissions.push({
      id: newId("sub"),
      teamId: conversation.teamId,
      conversationId: conversation.id,
      botId: conversation.botId,
      status: "pending",
      reason: null,
      pointsAwarded: null,
      createdAt: iso(now),
      gradedAt: null,
      gradingAt: now + 15_000 * (i + 1),
      finishAt: now + 45_000 * (i + 1),
    });
  });

  return store;
}

const holder = globalThis as unknown as { __mockStore?: Store };

/** In memory: survives hot reload, resets when the server restarts. */
export function getStore(): Store {
  holder.__mockStore ??= seed();
  return holder.__mockStore;
}
