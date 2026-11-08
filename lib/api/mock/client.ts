import type { ApiClient } from "@/lib/api/client";
import { readNdjson, toApiError } from "@/lib/api/errors";

// Browser side of the mock. All data and role checks live server-only in
// ./server and are reached through /api/mock/*, so admin-only fields are never
// in the JavaScript bundle that participants download.
export const mockApi = new Proxy({} as ApiClient, {
  get(_target, method: string) {
    return async (...args: unknown[]) => {
      const res = await fetch(`/api/mock/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ args }),
      });
      if (!res.ok) throw await toApiError(res);
      return method === "sendMessage" ? readNdjson(res) : res.json();
    };
  },
});
