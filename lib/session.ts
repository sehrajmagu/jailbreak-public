import { jwtVerify, SignJWT } from "jose";
import type { Session } from "@/lib/types";

export const SESSION_COOKIE = "session";
const MAX_AGE_SECONDS = 60 * 60 * 12;

export const SESSION_COOKIE_OPTIONS = {
  httpOnly: true,
  sameSite: "lax",
  secure: process.env.NODE_ENV === "production",
  path: "/",
  maxAge: MAX_AGE_SECONDS,
} as const;

// With no secret outside mock mode, every token fails verification.
function key(): Uint8Array | null {
  const secret =
    process.env.SESSION_SECRET ??
    (process.env.NEXT_PUBLIC_USE_MOCK === "true" ? "mock-only-secret" : null);
  return secret ? new TextEncoder().encode(secret) : null;
}

export async function signSession(session: Session): Promise<string> {
  const secret = key();
  if (!secret) throw new Error("SESSION_SECRET is not set");
  return new SignJWT({ ...session })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime(`${MAX_AGE_SECONDS}s`)
    .sign(secret);
}

export async function verifySession(
  token: string | undefined,
): Promise<Session | null> {
  const secret = key();
  if (!token || !secret) return null;
  try {
    const { payload } = await jwtVerify(token, secret, { algorithms: ["HS256"] });
    if (payload.role === "admin" && typeof payload.username === "string") {
      return { role: "admin", username: payload.username };
    }
    if (
      payload.role === "team" &&
      typeof payload.teamId === "string" &&
      typeof payload.teamName === "string"
    ) {
      return { role: "team", teamId: payload.teamId, teamName: payload.teamName };
    }
    return null;
  } catch {
    return null;
  }
}
