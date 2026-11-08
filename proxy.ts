import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySession } from "@/lib/session";

const TEAM_HOME = "/dashboard";
const ADMIN_HOME = "/admin";

// A UX guard only: API endpoints must still enforce the role themselves.
export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const session = await verifySession(request.cookies.get(SESSION_COOKIE)?.value);
  const home = session?.role === "admin" ? ADMIN_HOME : session ? TEAM_HOME : null;

  const redirect = (path: string) =>
    NextResponse.redirect(new URL(path, request.url));

  if (pathname === "/login" || pathname === "/admin/login") {
    return home ? redirect(home) : NextResponse.next();
  }
  if (pathname === "/") {
    return redirect(home ?? "/login");
  }
  if (pathname === "/admin" || pathname.startsWith("/admin/")) {
    if (session?.role === "admin") return NextResponse.next();
    return redirect(home ?? "/admin/login");
  }
  if (session?.role === "team") return NextResponse.next();
  return redirect(home ?? "/login");
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
