Detailed backlog, for reference.

Nothing here is built yet unless it says so. Work through it in this order, because each stage depends on the one before it.

### 1. Decisions to make first

These change what gets built, so settle them before backend work starts.

| Decision | Options | Why it matters |
| --- | --- | --- |
| Spending budgets | **Settled:** shared per-team dollar allowances through LiteLLM | Token usage remains recorded, with no per-model team quota |
| Config changes during the event | Edit in the backend or database, or build an admin screen | Prize amounts, `requiredBots`, `maxWinners` and level order have no editing screen. `/admin/prizes` only displays them |
| Final model tiers | Confirm the names and which model sits behind each bot | `GPT-6 Nano`, `Mini`, `Base` and `Pro` are placeholders. If names change, update `ModelName` in `lib/types.ts` and `MODELS` in `lib/constants.ts` |
| Point values | Confirm them | The 100 to 500 points per level are placeholders |

### 2. Backend: foundation

Implement everything in [Endpoints](FRONTEND.md#endpoints), returning the shapes in `lib/types.ts`. The mock in `lib/api/mock/server/handlers.ts` is a working reference for the logic, so port its rules rather than reinventing them.

- **Database:** store teams, bots, conversations, messages, tool calls, submissions, level overrides and level config. The mock keeps all of this in memory, so `store.ts` shows the fields you need.
- **Auth:** hash team and admin passwords, and rate-limit login attempts. Set the session cookie exactly as described in [Session cookie](FRONTEND.md#session-cookie).
- **Role checks on every endpoint:** do this in shared middleware, so a new endpoint can't forget it. Take the team ID only from the session, never from the request.
- **Separate response types:** map database rows to participant types field by field, as the mappers in `handlers.ts` do. Returning a whole row is how a system prompt or password would leak.
- **Same-site hosting:** serve the API on the same site as the frontend, or `proxy.ts` can't read the cookie. A reverse proxy or a Next.js rewrite from `/api` both work.
- **Disabled teams:** check on every request, not just at login, and return 401. A valid cookie must stop working as soon as a team is disabled.

### 3. Backend: real bots

This replaces the canned replies in `sendMessage`.

- **Model routing:** look up the bot's model on the server, then call the provider. Keep API keys out of the frontend and out of any `NEXT_PUBLIC_` variable.
- **System prompts:** write the 30 real prompts and defences, and load them on the server only. Those in `content.ts` are placeholders.
- **Conversation context:** send the conversation's history to the model on each message, and decide what happens when it outgrows the model's context window. Set a maximum message length too: neither the UI nor the mock has one, so one huge message could burn a team's tokens.
- **Streaming:** forward replies as newline-delimited `ChatEvent`s, as described in [Streaming](FRONTEND.md#streaming). Return rate-limit and lock errors before the stream begins.
- **Failures mid-stream:** if a provider fails partway, send the `error` event described in [Streaming](FRONTEND.md#streaming), and log the real error privately.
- **Spending limits:** use LiteLLM for team dollar budgets; keep recording token usage.
- **Rate limiting:** enforce per team and return `retryAfterSeconds`. The mock's limit is 6 messages in 30 seconds, which is a placeholder.

### 4. Backend: grading and prizes

- **Answer check:** implement `POST /bots/:id/answer`. Compare on the server, and record every attempt as a submission with `conversationId: null`.
- **Judge:** connected (`app/judging.py`, `supabase/judge.sql`), moving each submission through `pending`, `grading`, then `passed` or `failed`. Still needed: choose a real model, and run the real-model tests with real rubrics.
- **Judge security:** treat the conversation as untrusted, because teams will try to write messages that talk the judge into passing them. Clearly separate transcript from instructions, return structured output, and test this directly before the event.
- **Judge reasons:** the reason goes straight to teams, so it must not quote the rubric or the system prompt.
- **Regrading:** done. A regrade must recalculate points without awarding them twice. Points go only to a team's first pass on a bot.
- **Level completion:** port `levelState`, `prizeFor` and `isLevelLocked` from `handlers.ts`, including overrides and `maxWinners`. See [Rules the mock assumes](FRONTEND.md#rules-the-mock-assumes).
- **Prize payment records:** `/admin/prizes` shows what's owed, but nothing records who has been paid, or who granted or revoked a prize. You'll want at least a log of overrides, since they involve money.

### 5. Connect the frontend

1. Follow [Switching to the real backend](FRONTEND.md#switching-to-the-real-backend).
2. Test `lib/api/http.ts` against the real API. It type-checks but has never talked to a real server, so expect small fixes.
3. Click through every screen, and repeat the checks that passed on the mock:
   - A team can't open admin pages or call admin endpoints, and the reverse.
   - A team requesting another team's conversation gets 404.
   - Locked bots return 403, and disabled bots return 404.
   - The gateway dollar budget, rate-limit and disabled-team states all appear correctly.
4. Inspect the raw team responses in the browser's network tab, and search them for `systemPrompt`, `winRubric`, `secret` and `defences`.
5. Remove the demo accounts from this README once they no longer apply.

The mock can stay in the repo for frontend work, since it returns 404 unless `NEXT_PUBLIC_USE_MOCK=true`. **Never deploy with it on**, because it uses known passwords and a hardcoded signing key. Setting `NEXT_PUBLIC_USE_MOCK=false` for the production build also keeps it out of use, and the mock's data stays server-side either way.

### 6. Frontend gaps

- **Tests:** the repo has no automated tests or CI. At minimum, add API tests for role checks, team isolation and the participant response shapes, since those protect the event's integrity.
- **Accessibility:** labels, focus states and keyboard use are built in, but nothing has been tested with a screen reader.
- **Browsers:** testing so far has been in Chrome only. Check Safari and Firefox, and real phones, since some teams may use them.
- **Repo visibility:** this repo holds the mock's prompts and `open sesame`. Keep it private if participants might find it, and never commit the real prompts or passwords.

### 7. Before the event

- **Deployment:** host the frontend and backend, set `SESSION_SECRET` to a strong random value shared by both, and use HTTPS, since the cookie is `Secure` in production.
- **Load test:** simulate every team chatting at once, and check provider rate limits on each model. A provider limit during the event would look like a broken bot.
- **Cost estimate:** work out the worst-case API spend from team dollar allowances and the team count, and set a spending cap with each provider.
- **Bot testing:** solve every bot yourselves. Each must be beatable and must not break too easily, and judge bots should pass and fail the conversations you'd expect.
- **Accounts:** create real teams at `/admin/teams` and plan how to hand out passwords. Teams can't change their own, and a reset shows a new one to the admin once.
- **Dry run:** run a rehearsal with a few volunteer teams, including a submission, a regrade, a prize revoke and a team exhausting its shared dollar budget.
- **Monitoring:** log provider errors, judge failures and queue length, and decide who is on call during the event.
- **Backup:** back up the database during the event, since scores and prizes depend on it. Decide in advance what happens if a provider goes down mid-event.
