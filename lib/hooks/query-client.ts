import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

// The session is gone or the team was disabled. Clear the cookie first, or the
// route guard would send the user straight back from the login page.
function handleError(error: Error) {
  if (error instanceof ApiError && error.code === "unauthorized") {
    const path = window.location.pathname;
    if (path === "/login" || path === "/admin/login") return;
    const target = path.startsWith("/admin") ? "/admin/login" : "/login";
    api.logout().finally(() => window.location.assign(target));
  }
}

export function makeQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        // Only transient failures are worth retrying.
        retry: (count, error) =>
          count < 2 && (!(error instanceof ApiError) || error.code === "server_error"),
      },
    },
  });
}
