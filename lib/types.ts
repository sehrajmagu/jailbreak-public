// Shared API contract. The backend should return exactly these shapes.
//
// Participant types (first section) must never gain fields for system prompts,
// passwords, rubrics, defences or judge details. Those belong only on the
// Admin* types below. Teams may see each bot's model and per-message token counts.

export type ApiErrorCode =
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "rate_limited"
  | "conflict"
  | "validation"
  | "server_error";

/** Body of every non-2xx response. */
export interface ApiErrorBody {
  error: { code: ApiErrorCode; message: string; retryAfterSeconds?: number };
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

// ---------- Auth ----------

export type Session =
  | { role: "team"; teamId: string; teamName: string }
  | { role: "admin"; username: string };

export interface TeamCredentials {
  /** Team name or team ID. */
  team: string;
  password: string;
}

export interface AdminCredentials {
  username: string;
  password: string;
}

// ---------- Participant ----------

/** "answer": a typed password checked by the server. "judge": a graded conversation. */
export type CheckType = "answer" | "judge";

export type ModelName =
  | "Astra"
  | "Terra"
  | "Sol"
  | "Luna"
  | "Open-source LLM"
  | "Fable 5.1";

export type BotStatus = "locked" | "unattempted" | "attempted" | "solved";

export interface TeamProfile {
  id: string;
  name: string;
  /** Sum across all models, for information. Spending is limited by TeamBudget. */
  tokensUsed: number;
}

/** US dollars, from LiteLLM. One allowance, shared across the team and every model. */
export interface TeamBudget {
  budgetUsd: number;
  spentUsd: number;
  remainingUsd: number;
}

export interface DashboardSummary {
  points: number;
  rank: number | null;
  totalTeams: number;
  /** Bots with at least one conversation, including solved ones. */
  attempted: number;
  solved: number;
  /** Enabled bots not solved yet. */
  remaining: number;
  levelsCompleted: number;
  totalLevels: number;
  /** Rupees. */
  prizeEarned: number;
  /** This team's usage only, with one entry per model, including models at zero. */
  usageByModel: ModelUsage[];
}

export interface ModelUsage {
  model: string;
  /** Input + output tokens. */
  tokensUsed: number;
}

export interface BotCard {
  id: string;
  level: number;
  /** "Bot A" to "Bot F". */
  label: string;
  /** The model's display name. */
  model: ModelName;
  checkType: CheckType;
  points: number;
  status: BotStatus;
}

export interface BotDetail extends BotCard {
  levelName: string;
  description: string;
  goal: string;
  /** Answer bots only: this team's wrong answers on this bot so far. */
  wrongAnswersUsed: number;
  /** Answer bots only: the answer box closes once this many are used. */
  maxWrongAnswers: number;
}

export interface LevelProgress {
  level: number;
  name: string;
  locked: boolean;
  /** Bots solved in this level. */
  solved: number;
  /** Bots needed to complete the level. */
  required: number;
  /** Enabled bots in this level. */
  total: number;
  complete: boolean;
  /** An organiser revoked this level for the team. */
  revoked: boolean;
  /** Rupees offered for completing the level. */
  prizeAmount: number;
  /** Rupees this team has earned here. */
  prizeAwarded: number;
  bots: BotCard[];
}

export interface ToolCall {
  id: string;
  name: string;
  /** JSON-encoded arguments. */
  arguments: string;
  result: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: ToolCall[];
  tokens: number;
  createdAt: string;
}

export type SubmissionStatus = "pending" | "grading" | "passed" | "failed";

export interface Submission {
  id: string;
  /** Null for answer checks, which aren't tied to a conversation. */
  conversationId: string | null;
  botId: string;
  level: number;
  botLabel: string;
  checkType: CheckType;
  status: SubmissionStatus;
  /** Judge's reason, or the answer check's result. Null until graded. */
  reason: string | null;
  /** Null until graded. */
  pointsAwarded: number | null;
  createdAt: string;
  gradedAt: string | null;
}

export interface AnswerResult {
  correct: boolean;
  pointsAwarded: number;
}

export interface ConversationSummary {
  id: string;
  botId: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
  submissionStatus: SubmissionStatus | null;
}

export interface Conversation extends ConversationSummary {
  messages: ChatMessage[];
  submission: Submission | null;
}

export interface SendMessageRequest {
  botId: string;
  /** Null starts a new conversation. */
  conversationId: string | null;
  content: string;
}

/** Newline-delimited JSON events streamed by sendMessage. */
export type ChatEvent =
  | {
      type: "start";
      conversationId: string;
      userMessage: ChatMessage;
      assistantMessageId: string;
    }
  | { type: "tool_call"; toolCall: ToolCall }
  | { type: "delta"; text: string }
  | { type: "done"; message: ChatMessage }
  /**
   * The reply failed partway. Ends the stream instead of "done". The turn isn't
   * shown in history. Provider work may still cost money. Only safe fixed codes
   * reach the browser; the UI supplies the wording.
   */
  | { type: "error"; code: "bot_unavailable" | "budget_exceeded" };

export interface LeaderboardEntry {
  rank: number;
  teamId: string;
  teamName: string;
  score: number;
  solved: number;
  isOwnTeam: boolean;
}

// ---------- Admin only ----------

export interface AdminBot {
  id: string;
  label: string;
  level: number;
  model: ModelName;
  checkType: CheckType;
  points: number;
  description: string;
  goal: string;
  systemPrompt: string;
  /** Used when checkType is "judge". */
  winRubric: string;
  /** The password. Used when checkType is "answer". */
  secret: string;
  defences: string[];
  enabled: boolean;
}

export type BotInput = Omit<AdminBot, "id">;

export interface AdminModel {
  model: ModelName;
}

/** Organisers only. Teams never see whether judging is on. */
export interface AdminJudging {
  /** Off: submissions are accepted and wait, but nothing is sent to the judge. */
  enabled: boolean;
  /** Set while grading waits out a judge rate limit or outage. */
  pausedUntil: string | null;
  updatedAt: string | null;
  /** Submitted and still to be graded, including ones waiting to retry. */
  waiting: number;
  /** Stuck on an error a retry can't fix. Fix the cause, then regrade them. */
  needsOrganiser: number;
}


export interface AdminTeam {
  id: string;
  name: string;
  disabled: boolean;
  /** Sum across all models. */
  tokensUsed: number;
  attempted: number;
  solved: number;
  score: number;
  rank: number | null;
  lastActiveAt: string | null;
  createdAt: string;
}

export interface TeamBotProgress {
  botId: string;
  label: string;
  level: number;
  model: ModelName;
  status: BotStatus;
  conversations: number;
}

export interface AdminTeamDetail extends AdminTeam {
  progress: TeamBotProgress[];
}

export interface CreateTeamInput {
  name: string;
  password: string;
}

export interface UpdateTeamInput {
  name?: string;
  disabled?: boolean;
}

export interface AdminSubmission extends Submission {
  teamId: string;
  teamName: string;
  model: ModelName;
}

export type ConversationResult =
  | SubmissionStatus
  | "not_submitted"
  /** On an answer bot, where results come from the answer box instead. */
  | "answer_bot";

export interface AdminConversationSummary extends ConversationSummary {
  teamId: string;
  teamName: string;
  botLabel: string;
  level: number;
  model: ModelName;
  tokens: number;
  result: ConversationResult;
}

/** Admins also see failed turns, which teams never do. */
export interface AdminChatMessage extends ChatMessage {
  status: "complete" | "failed";
}

export interface AdminConversation extends AdminConversationSummary {
  messages: AdminChatMessage[];
  verdict: AdminSubmission | null;
}

export interface ConversationFilters {
  teamId?: string;
  botId?: string;
  model?: ModelName;
  result?: ConversationResult;
  page?: number;
  pageSize?: number;
}

export interface SubmissionFilters {
  status?: SubmissionStatus;
  teamId?: string;
  page?: number;
  pageSize?: number;
}

export interface OverviewPoint {
  /** UTC day, YYYY-MM-DD. */
  date: string;
  attempts: number;
  solves: number;
  tokensConsumed: number;
}

export interface AdminOverview {
  totalTeams: number;
  /** Teams with activity in the last 24 hours. */
  activeTeams: number;
  tokensConsumed: number;
  /** One attempt is one conversation. */
  totalAttempts: number;
  /** Distinct team and bot pairs with a passed submission. */
  totalSolves: number;
  series: OverviewPoint[];
}

export interface BotStat {
  botId: string;
  label: string;
  level: number;
  model: ModelName;
  enabled: boolean;
  attempts: number;
  teamsAttempted: number;
  solves: number;
  /** Solves divided by teamsAttempted, from 0 to 1. */
  solveRate: number;
  /** Mean conversations per solving team. Null when nobody has solved it. */
  avgAttemptsToSolve: number | null;
  tokensUsed: number;
}

export interface LevelConfig {
  level: number;
  name: string;
  /** Rupees. */
  prizeAmount: number;
  /** The N in "any N of M bots". Set it to M to require all of them. */
  requiredBots: number;
  /** Only the first N teams to complete the level win a prize. Null is no limit. */
  maxWinners: number | null;
}

/** "granted" completes the level regardless of solves. "revoked" cancels completion. */
export type LevelOverride = "granted" | "revoked";

export interface AdminLevelCompletion {
  level: number;
  solved: number;
  required: number;
  total: number;
  /** The solves alone meet the requirement. */
  earned: boolean;
  override: LevelOverride | null;
  complete: boolean;
  prizeAwarded: number;
  completedAt: string | null;
}

export interface AdminTeamPrizes {
  teamId: string;
  teamName: string;
  levels: AdminLevelCompletion[];
  prizeOwed: number;
}

export interface AdminPrizes {
  /** Each level opens once the previous one is complete. */
  requireLevelsInOrder: boolean;
  levels: LevelConfig[];
  teams: AdminTeamPrizes[];
  totalPrizeOwed: number;
}
