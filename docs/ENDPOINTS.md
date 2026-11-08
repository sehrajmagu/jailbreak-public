# API endpoints

Every endpoint the frontend calls, taken from [`lib/api/client.ts`](../lib/api/client.ts), with paths from [`lib/api/http.ts`](../lib/api/http.ts) and types from [`lib/types.ts`](../lib/types.ts). There are 35: 4 auth, 11 team and 20 admin.

Paths are relative to `NEXT_PUBLIC_API_URL` (default `/api`). Errors use the format in the [frontend guide](FRONTEND.md#errors).

**Access:** public needs no session. Team and admin need a session cookie with that role. Team endpoints identify the team from the session only, and never take a team ID.

## Auth

| Method | Path | Access | Request | Response | Purpose |
| --- | --- | --- | --- | --- | --- |
| POST | `/auth/login` | Public | `TeamCredentials` | `Session` | Sign a team in and set the session cookie. Too many wrong passwords gets 429 |
| POST | `/auth/admin/login` | Public | `AdminCredentials` | `Session` | Sign an admin in and set the session cookie. Too many wrong passwords gets 429 |
| POST | `/auth/logout` | Public | None | None (204) | Clear the session cookie |
| GET | `/auth/session` | Public | None | `Session \| null` | Return the current session, if any |

## Team

| Method | Path | Access | Request | Response | Purpose |
| --- | --- | --- | --- | --- | --- |
| GET | `/me` | Team | None | `TeamProfile` | Get the team's name and total tokens used |
| GET | `/me/budget` | Team | None | `TeamBudget` | Get the team's own dollar budget, spend and remainder from LiteLLM. 503 if LiteLLM can't say right now |
| GET | `/me/dashboard` | Team | None | `DashboardSummary` | Get points, rank, progress, prizes and the team's own token usage and limit per model |
| GET | `/levels` | Team | None | `LevelProgress[]` | List levels with their bots and progress |
| GET | `/bots/:id` | Team | None | `BotDetail` | Get one bot's brief, wrong-answer count and whether its model's limit is reached |
| GET | `/bots/:id/conversations` | Team | None | `ConversationSummary[]` | List the team's conversations with a bot |
| GET | `/conversations/:id` | Team | None | `Conversation` | Get one of the team's conversations |
| POST | `/messages` | Team | `SendMessageRequest` | `ChatEvent` stream (NDJSON) | Send a message and stream the reply. Refused once the bot's model reaches its limit |
| POST | `/conversations/:id/submit` | Team | None | `Submission` | Submit a conversation to the judge |
| POST | `/bots/:id/answer` | Team | `{ answer: string }` | `AnswerResult` | Check a password on an answer bot |
| GET | `/submissions` | Team | None | `Submission[]` | List the team's submissions |
| GET | `/leaderboard` | Team | None | `LeaderboardEntry[]` | Get the team ranking |

## Admin

| Method | Path | Access | Request | Response | Purpose |
| --- | --- | --- | --- | --- | --- |
| GET | `/admin/overview` | Admin | None | `AdminOverview` | Get event totals and daily activity |
| GET | `/admin/teams` | Admin | None | `AdminTeam[]` | List all teams |
| GET | `/admin/teams/:id` | Admin | None | `AdminTeamDetail` | Get a team and its progress on each bot |
| POST | `/admin/teams` | Admin | `CreateTeamInput` | `AdminTeam` | Create a team |
| PATCH | `/admin/teams/:id` | Admin | `UpdateTeamInput` | `AdminTeam` | Rename, disable or enable a team |
| POST | `/admin/teams/:id/reset-password` | Admin | None | `{ temporaryPassword: string }` | Reset a team's password |
| GET | `/admin/conversations` | Admin | `ConversationFilters` (query) | `Paginated<AdminConversationSummary>` | Search conversations across all teams |
| GET | `/admin/conversations/:id` | Admin | None | `AdminConversation` | Get a full conversation and its verdict |
| GET | `/admin/submissions` | Admin | `SubmissionFilters` (query) | `Paginated<AdminSubmission>` | Search submissions across all teams |
| POST | `/admin/submissions/:id/regrade` | Admin | None | `AdminSubmission` | Send a judged submission for regrading |
| GET | `/admin/judging` | Admin | None | `AdminJudging` | Whether judging is on, and the queue |
| PUT | `/admin/judging` | Admin | `{ enabled }` | `AdminJudging` | Turn judging on or off at once. Off still accepts submissions |
| GET | `/admin/bot-stats` | Admin | None | `BotStat[]` | Get attempts, solves and tokens per bot |
| GET | `/admin/bots` | Admin | None | `AdminBot[]` | List all bots, including hidden fields |
| GET | `/admin/bots/:id` | Admin | None | `AdminBot` | Get one bot, including hidden fields |
| GET | `/admin/models` | Admin | None | `AdminModel[]` | List model names |
| PATCH | `/admin/models/:model` | Admin | None | 410 Gone | Retired: manage dollar budgets in LiteLLM |
| POST | `/admin/bots` | Admin | `BotInput` | `AdminBot` | Create a bot |
| PUT | `/admin/bots/:id` | Admin | `BotInput` | `AdminBot` | Replace a bot's settings |
| PATCH | `/admin/bots/:id` | Admin | `{ enabled: boolean }` | `AdminBot` | Enable or disable a bot |
| GET | `/admin/prizes` | Admin | None | `AdminPrizes` | Get level completions and prizes owed |
| PUT | `/admin/prizes/:teamId/levels/:level` | Admin | `{ override: LevelOverride \| null }` | `AdminPrizes` | Grant, revoke or clear a level override |
