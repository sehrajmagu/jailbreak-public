import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { ApiError, STATUS_BY_CODE } from "@/lib/api/errors";
import {
  adminHandlers,
  advanceSubmissions,
  authHandlers,
  isActiveTeam,
  teamHandlers,
} from "@/lib/api/mock/server/handlers";
import {
  SESSION_COOKIE,
  SESSION_COOKIE_OPTIONS,
  signSession,
  verifySession,
} from "@/lib/session";
import type { Session } from "@/lib/types";

const MIN_LATENCY_MS = 120;
const MAX_LATENCY_MS = 500;
const ERROR_RATE = Number(process.env.MOCK_ERROR_RATE ?? 0.02);

type Handler = (...args: unknown[]) => Promise<unknown>;
const lookup = (handlers: object, method: string) =>
  Object.hasOwn(handlers, method)
    ? ((handlers as Record<string, Handler>)[method] as Handler)
    : null;

function isStream(value: unknown): value is AsyncIterable<unknown> {
  return typeof value === "object" && value !== null && Symbol.asyncIterator in value;
}

function ndjson(events: AsyncIterable<unknown>): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      for await (const event of events) {
        controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "application/x-ndjson", "Cache-Control": "no-store" },
  });
}

async function dispatch(method: string, args: unknown[], session: Session | null) {
  const team = lookup(teamHandlers, method);
  if (team) {
    if (!session) throw new ApiError("unauthorized", "Please sign in.");
    if (session.role !== "team") throw new ApiError("forbidden", "Team access only.");
    if (!isActiveTeam(session)) throw new ApiError("unauthorized", "This team is not active.");
    return team(session.teamId, ...args);
  }
  const admin = lookup(adminHandlers, method);
  if (admin) {
    if (!session) throw new ApiError("unauthorized", "Please sign in.");
    if (session.role !== "admin") throw new ApiError("forbidden", "Admin access only.");
    return admin(...args);
  }
  throw new ApiError("not_found", "Unknown endpoint.");
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ method: string }> },
) {
  if (process.env.NEXT_PUBLIC_USE_MOCK !== "true") {
    return new NextResponse(null, { status: 404 });
  }
  const { method } = await params;
  const { args = [] } = (await request.json().catch(() => ({}))) as { args?: unknown[] };
  const store = await cookies();
  const session = await verifySession(store.get(SESSION_COOKIE)?.value);

  await new Promise((resolve) =>
    setTimeout(resolve, MIN_LATENCY_MS + Math.random() * (MAX_LATENCY_MS - MIN_LATENCY_MS)),
  );

  try {
    advanceSubmissions();

    if (method === "getSession") return NextResponse.json(session);
    if (method === "logout") {
      store.delete(SESSION_COOKIE);
      return NextResponse.json(null);
    }
    const auth = lookup(authHandlers, method);
    if (auth) {
      const created = (await auth(...args)) as Session;
      store.set(SESSION_COOKIE, await signSession(created), SESSION_COOKIE_OPTIONS);
      return NextResponse.json(created);
    }

    if (Math.random() < ERROR_RATE) {
      throw new ApiError("server_error", "The server hit a temporary error. Please try again.");
    }
    const result = await dispatch(method, args, session);
    return isStream(result) ? ndjson(result) : NextResponse.json(result ?? null);
  } catch (error) {
    const apiError =
      error instanceof ApiError
        ? error
        : new ApiError("server_error", "Something went wrong. Please try again.");
    if (!(error instanceof ApiError)) console.error(error);
    return NextResponse.json(
      {
        error: {
          code: apiError.code,
          message: apiError.message,
          retryAfterSeconds: apiError.retryAfterSeconds,
        },
      },
      { status: STATUS_BY_CODE[apiError.code] },
    );
  }
}
