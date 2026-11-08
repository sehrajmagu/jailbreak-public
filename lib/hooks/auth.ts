"use client";

import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AdminCredentials, TeamCredentials } from "@/lib/types";

// Login and logout use full page loads so that no cached data from one session
// can carry over to the next.

export function useTeamLogin() {
  return useMutation({
    mutationFn: (credentials: TeamCredentials) => api.login(credentials),
    onSuccess: () => window.location.assign("/dashboard"),
  });
}

export function useAdminLogin() {
  return useMutation({
    mutationFn: (credentials: AdminCredentials) => api.adminLogin(credentials),
    onSuccess: () => window.location.assign("/admin"),
  });
}

export function useLogout(redirectTo: string) {
  return useMutation({
    mutationFn: () => api.logout(),
    onSuccess: () => window.location.assign(redirectTo),
  });
}
