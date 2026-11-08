import type {
  AdminBot,
  AdminJudging,
  AdminModel,
  AdminConversation,
  AdminConversationSummary,
  AdminCredentials,
  AdminOverview,
  AdminPrizes,
  AdminSubmission,
  AdminTeam,
  AdminTeamDetail,
  AnswerResult,
  BotDetail,
  BotInput,
  BotStat,
  ChatEvent,
  Conversation,
  ConversationFilters,
  ConversationSummary,
  CreateTeamInput,
  DashboardSummary,
  TeamBudget,
  LeaderboardEntry,
  LevelOverride,
  LevelProgress,
  Paginated,
  SendMessageRequest,
  Session,
  Submission,
  SubmissionFilters,
  TeamCredentials,
  TeamProfile,
  UpdateTeamInput,
} from "@/lib/types";

export interface AuthApi {
  /** Sets the httpOnly session cookie. */
  login(credentials: TeamCredentials): Promise<Session>;
  /** Sets the httpOnly session cookie. */
  adminLogin(credentials: AdminCredentials): Promise<Session>;
  logout(): Promise<void>;
  getSession(): Promise<Session | null>;
}

/** Scoped to the team in the session. Never takes a team ID. */
export interface TeamApi {
  getMe(): Promise<TeamProfile>;
  getBudget(): Promise<TeamBudget>;
  getDashboard(): Promise<DashboardSummary>;
  getLevels(): Promise<LevelProgress[]>;
  getBot(id: string): Promise<BotDetail>;
  getConversations(botId: string): Promise<ConversationSummary[]>;
  getConversation(id: string): Promise<Conversation>;
  /** Rejects before streaming on rate limit, a locked level. */
  sendMessage(request: SendMessageRequest): Promise<AsyncIterable<ChatEvent>>;
  /** For judge bots. */
  submitConversation(conversationId: string): Promise<Submission>;
  /** For answer bots. */
  submitAnswer(botId: string, answer: string): Promise<AnswerResult>;
  getSubmissions(): Promise<Submission[]>;
  getLeaderboard(): Promise<LeaderboardEntry[]>;
}

/** Requires an admin session. */
export interface AdminApi {
  getAdminOverview(): Promise<AdminOverview>;
  getAdminTeams(): Promise<AdminTeam[]>;
  getAdminTeam(id: string): Promise<AdminTeamDetail>;
  createTeam(input: CreateTeamInput): Promise<AdminTeam>;
  updateTeam(id: string, input: UpdateTeamInput): Promise<AdminTeam>;
  /** Returns a temporary password, shown to the admin once. */
  resetTeamPassword(id: string): Promise<{ temporaryPassword: string }>;
  getAdminConversations(
    filters: ConversationFilters,
  ): Promise<Paginated<AdminConversationSummary>>;
  getAdminConversation(id: string): Promise<AdminConversation>;
  getAdminSubmissions(
    filters: SubmissionFilters,
  ): Promise<Paginated<AdminSubmission>>;
  regradeSubmission(id: string): Promise<AdminSubmission>;
  getJudging(): Promise<AdminJudging>;
  /** Takes effect at once, with no restart. */
  setJudging(enabled: boolean): Promise<AdminJudging>;
  getBotStats(): Promise<BotStat[]>;
  getAdminBots(): Promise<AdminBot[]>;
  getAdminBot(id: string): Promise<AdminBot>;
  createBot(input: BotInput): Promise<AdminBot>;
  updateBot(id: string, input: BotInput): Promise<AdminBot>;
  setBotEnabled(id: string, enabled: boolean): Promise<AdminBot>;
  getAdminModels(): Promise<AdminModel[]>;
  getAdminPrizes(): Promise<AdminPrizes>;
  /** Null clears the override, so the team's solves decide again. */
  setLevelOverride(
    teamId: string,
    level: number,
    override: LevelOverride | null,
  ): Promise<AdminPrizes>;
}

export type ApiClient = AuthApi & TeamApi & AdminApi;
