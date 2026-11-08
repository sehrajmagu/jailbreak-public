# Frontend and API contract

The Next.js frontend, the mock API, and the contract between the frontend and the backend. For an overview of the whole project, see the [main README](../README.md).

By default the app runs against a built-in mock API. Set `NEXT_PUBLIC_USE_MOCK=false` to use the real backend: see [backend/README.md](../backend/README.md).

## Run it

```bash
npm install
cp .env.example .env.local
npm run dev
```

Open http://localhost:3000.

### Demo accounts (mock only)

| Role | Sign in at | Username | Password |
| --- | --- | --- | --- |
| Team | `/login` | `Prompt Pirates` or `t-01` (teams run `t-01` to `t-12`) | `example-password-demo` |
| Admin | `/admin/login` | `admin` | `example-password-admin` |

### Mock behaviour worth knowing

- **Solving an answer bot (Level 1):** include `open sesame` in a message and the bot reveals its password. Type that into the answer box.
- **Solving a judge bot (Levels 2 to 5):** include `open sesame` in a message and submit the conversation. Any other conversation fails.
- **Rate limit:** more than 6 messages in 30 seconds per team.
- **Failed replies:** include `simulate failure` in a message and the reply fails partway. 2% of replies also fail at random.
- **Latency and errors:** every call waits 120 to 500 ms, and 2% of calls fail at random to exercise the error states. Set `MOCK_ERROR_RATE=0` in `.env.local` to turn off both kinds of random failure.
- **Data:** generated from a fixed seed and held in memory. Restarting the server resets it.

## Folder structure

```
app/
  (team)/                 Participant routes: /dashboard, /levels, /bots/[id], /submissions, /leaderboard
  admin/(dash)/           Admin routes: /admin, /admin/teams, /admin/conversations, /admin/submissions, /admin/prizes, /admin/bot-stats, /admin/bots
  login/, admin/login/    Sign-in pages
  api/mock/[method]/      Mock API endpoint. Returns 404 unless mock mode is on
components/
  ui.tsx                  Buttons, fields, cards, dialogs, tables, loading and empty states
  team/, admin/           Components for one side of the app
lib/
  types.ts                Shared API types. Backend should match these
  constants.ts            Levels, bot labels, models and statuses for labels and filters
  session.ts              Session cookie signing and verification
  api/
    client.ts             The ApiClient interface: one function per endpoint
    http.ts               Real backend implementation
    mock/client.ts        mockApi, the browser side of the mock
    mock/server/          Mock data, role checks and business rules (server-only)
    mock/server/content.ts  Event config: prizes, N of M, maxWinners, level unlocking
    index.ts              Picks the implementation from NEXT_PUBLIC_USE_MOCK
  hooks/                  React Query hooks. The only way components get data
proxy.ts                  Route guards (the Next.js 16 name for middleware)
```

Data flows one way: component → `lib/hooks` → `lib/api` → backend. Components never call `fetch` or `api` directly.

## Security model

- **Participant types have no secret fields.** `BotDetail` and friends in `lib/types.ts` can't hold a system prompt, rubric, password, defences or judge details. Only `Admin*` types can.
- **The mock's data is server-only.** `lib/api/mock/server/` imports `server-only`, so the build fails if a client component imports it. Hidden prompts are never in the browser bundle, even in mock mode.
- **Route guards are a convenience, not the protection.** `proxy.ts` keeps each role on its own pages, but **the backend must enforce the role and team on every endpoint**.
- **Teams are identified by the session only.** No team endpoint takes a team ID. A team requesting another team's conversation must get a 404.

### What teams can and can't see

Teams can see each bot's model and per-message token counts. **Never send these to a team:** system prompts, passwords, rubrics, defences or judge details.

## Decisions

- **Level 1 passwords:** each bot has its own distinct password, so there are 6 passwords, one per bot. All teams face the same 6.
- **Wrong answers:** capped at 10 per bot, per team (configurable). After that, the answer box and the chat are both closed for that bot, even if the team has already solved it. The UI shows "x of 10 wrong answers used".
- **Level access:** all levels are open from the start unless ordered unlocking is configured.
- **Spending budgets:** LiteLLM enforces each team's shared dollar allowance across models. Tokens are tracked as usage, without a per-model team quota. See [gateway setup](../deploy/litellm/README.md).
- **Models:** teams can see which model each bot uses. Bots in a level are ordered by model.

## Levels, bots and prizes

The event has 5 levels of 6 bots: 1 Password, 2 Forbidden topic, 3 Harmful language in Hindi, 4 Malware and 5 Explicit content. The frontend and mock still show older names until they're updated. Level 1 has `checkType: "answer"`: the team extracts the password and types it into an answer box, and the server compares it with no judge involved. Levels 2 to 5 have `checkType: "judge"`: the team submits a conversation for grading.

Config lives in `lib/api/mock/server/content.ts`, and there is no admin screen for editing it:

| Setting | Default | Meaning |
| --- | --- | --- |
| `requiredBots` (per level) | 3 | The N in "any N of M". Set it to 6 to require all bots |
| `prizeAmount` (per level) | 1500 | Prize in rupees |
| `maxWinners` (per level) | `null` | Only the first N teams to complete win the prize. `null` turns this off |
| `REQUIRE_LEVELS_IN_ORDER` | `false` | When `true`, each level opens once the previous one is complete. All 6 bots in an open level are available at once |
| `MAX_WRONG_ANSWERS` | 10 | Wrong answers allowed per bot, per team, before the answer box closes |

## Switching to the real backend

1. Implement the endpoints below on the backend.
2. Set these in `.env.local`:

   ```bash
   NEXT_PUBLIC_USE_MOCK=false
   NEXT_PUBLIC_API_URL=/api          # or a URL on the same site as the frontend
   SESSION_SECRET=<HS256 key shared with the backend>
   ```

3. Restart the server. `NEXT_PUBLIC_*` values are built into the bundle, so rebuild for production too.

No component or hook changes are needed. `lib/api/http.ts` is already written against this table. With `NEXT_PUBLIC_USE_MOCK=false`, `/api/mock/*` returns 404.

### Session cookie

On login the backend must set this cookie, because `proxy.ts` reads it to guard routes:

- Name `session`, flags `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`.
- Value: an HS256 JWT signed with `SESSION_SECRET`, with an `exp` claim and either `{ "role": "team", "teamId": "...", "teamName": "..." }` or `{ "role": "admin", "username": "..." }`.

The frontend's domain must be able to read the cookie, so serve the API from the same site. `SESSION_SECRET` goes in `.env.local` in the repo root as well as in `backend/.env`, because Next.js doesn't read `backend/`. If `SESSION_SECRET` is missing, every session is treated as invalid and all protected pages redirect to login.

### Errors

Every non-2xx response has the body `{ "error": { "code", "message", "retryAfterSeconds?" } }`:

| Status | Code | Meaning |
| --- | --- | --- |
| 401 | `unauthorized` | Not signed in, or the team was disabled. The UI logs out and redirects |
| 403 | `forbidden` | Wrong role, or the level is locked |
| 404 | `not_found` | Missing, or belongs to another team |
| 409 | `conflict` | For example, the conversation was already submitted |
| 422 | `validation` | Invalid input |
| 429 | `rate_limited` | Include `retryAfterSeconds` |
| 500 | `server_error` | Reads are retried automatically |

### Streaming

`POST /messages` responds with `application/x-ndjson`, one `ChatEvent` per line, in this order: `start`, then any `tool_call` and `delta` events, then exactly one `done` or `error`. Return rate-limit and lock errors as normal error responses before streaming begins.

If a reply fails after streaming has begun, send this as the last event:

```json
{"type":"error","code":"bot_unavailable"}
```

- **Safe error codes:** `budget_exceeded` tells the team its AI allowance is exhausted or insufficient for this request; other failures use `bot_unavailable`. Neither includes raw provider messages. The UI supplies fixed wording.
- **Failed turn bookkeeping:** the app's token ledger excludes failed turns, but provider work already completed may still consume the LiteLLM dollar budget. The UI discards the partial reply and puts the team's message back in the input box. The server keeps the turn for organisers, with its token usage, but hides it from the team and from later history, so a retry isn't duplicated. If it was the first turn of a new conversation, the team doesn't see that conversation either.
- **A provider refusal isn't a failure:** the model ran and tokens were spent, so it ends with `done` and a fixed in-character reply, and it's charged and rate-limited. Provider spend is recorded independently by LiteLLM, including billable work in failed turns.
- **Lost connections:** if the stream breaks or closes without `done` or `error`, the UI shows a separate error. It asks the team to check their conversation history before retrying, because the server may have finished the reply.

### Endpoints

Types are in [`lib/types.ts`](../lib/types.ts). `ApiClient` is in [`lib/api/client.ts`](../lib/api/client.ts).

| `ApiClient` function | Request | Returns |
| --- | --- | --- |
| **Auth** | | |
| `login` | `POST /auth/login` | `Session`, sets cookie |
| `adminLogin` | `POST /auth/admin/login` | `Session`, sets cookie |
| `logout` | `POST /auth/logout` | clears cookie |
| `getSession` | `GET /auth/session` | `Session \| null` |
| **Team** (team session) | | |
| `getMe` | `GET /me` | `TeamProfile` |
| `getBudget` | `GET /me/budget` | `TeamBudget` |
| `getDashboard` | `GET /me/dashboard` | `DashboardSummary` |
| `getLevels` | `GET /levels` | `LevelProgress[]` |
| `getBot` | `GET /bots/:id` | `BotDetail` |
| `getConversations` | `GET /bots/:id/conversations` | `ConversationSummary[]` |
| `getConversation` | `GET /conversations/:id` | `Conversation` |
| `sendMessage` | `POST /messages` | `ChatEvent` stream |
| `submitConversation` | `POST /conversations/:id/submit` | `Submission` |
| `submitAnswer` | `POST /bots/:id/answer` with `{ answer }` | `AnswerResult` (`{ correct, pointsAwarded }`) |
| `getSubmissions` | `GET /submissions` | `Submission[]` |
| `getLeaderboard` | `GET /leaderboard` | `LeaderboardEntry[]` |
| **Admin** (admin session) | | |
| `getAdminOverview` | `GET /admin/overview` | `AdminOverview` |
| `getAdminTeams` | `GET /admin/teams` | `AdminTeam[]` |
| `getAdminTeam` | `GET /admin/teams/:id` | `AdminTeamDetail` |
| `createTeam` | `POST /admin/teams` | `AdminTeam` |
| `updateTeam` | `PATCH /admin/teams/:id` | `AdminTeam` |
| `resetTeamPassword` | `POST /admin/teams/:id/reset-password` | `{ temporaryPassword }` |
| `getAdminConversations` | `GET /admin/conversations?teamId&botId&model&result&page&pageSize` | `Paginated<AdminConversationSummary>` |
| `getAdminConversation` | `GET /admin/conversations/:id` | `AdminConversation` |
| `getAdminSubmissions` | `GET /admin/submissions?status&teamId&page&pageSize` | `Paginated<AdminSubmission>` |
| `regradeSubmission` | `POST /admin/submissions/:id/regrade` | `AdminSubmission` |
| `getJudging` | `GET /admin/judging` | `AdminJudging` |
| `setJudging` | `PUT /admin/judging` with `{ enabled }` | `AdminJudging` |
| `getBotStats` | `GET /admin/bot-stats` | `BotStat[]` |
| `getAdminBots` | `GET /admin/bots` | `AdminBot[]` |
| `getAdminBot` | `GET /admin/bots/:id` | `AdminBot` |
| `createBot` | `POST /admin/bots` | `AdminBot` |
| `updateBot` | `PUT /admin/bots/:id` | `AdminBot` |
| `setBotEnabled` | `PATCH /admin/bots/:id` | `AdminBot` |
| `getAdminModels` | `GET /admin/models` | `AdminModel[]` |
| `updateModel` | `PATCH /admin/models/:model` | `AdminModel` |
| `getAdminPrizes` | `GET /admin/prizes` | `AdminPrizes` |
| `setLevelOverride` | `PUT /admin/prizes/:teamId/levels/:level` with `{ override }` | `AdminPrizes` |

### Rules the mock assumes

The backend should match these, or tell the frontend team if it differs:

- With `REQUIRE_LEVELS_IN_ORDER` on (it's off by default), a level opens once every earlier level is complete. Bots in a locked level appear in `getLevels`, but `getBot` and `sendMessage` return 403.
- Bots within an open level are all available at once. Disabled bots are hidden from teams.
- `submitAnswer` returns 409 on a judge bot, and `submitConversation` returns 409 on an answer bot. Answers are trimmed, then compared case-sensitively, on the server only.
- After `MAX_WRONG_ANSWERS` wrong answers on a bot, `submitAnswer` returns 409 for that team and bot, even if the answer is correct. `getBot` returns `wrongAnswersUsed` and `maxWrongAnswers`.
- Every answer attempt is recorded as a submission, with `conversationId: null`. Answer submissions can't be regraded.
- A judge conversation can be submitted once, and can't be continued after that. Answer bot conversations stay open.
- Points are awarded for the first pass on a bot. Later passes award 0.
- A level is complete when solves reach `requiredBots` (capped at the enabled bots), unless an admin override says otherwise. `granted` completes it, `revoked` cancels it and any prize, and `null` clears the override.
- The prize is awarded once per level, however many extra bots are solved. With `maxWinners` set, teams are ordered by when they completed the level.
- Disabled teams are removed from the leaderboard and can't sign in.
