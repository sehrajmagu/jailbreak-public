import type { NextConfig } from "next";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Serves the API from this site when the mock is off, so the session cookie and
  // the calls in lib/api/http.ts work in development.
  async rewrites() {
    if (process.env.NEXT_PUBLIC_USE_MOCK === "true") return [];
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
};

export default nextConfig;
