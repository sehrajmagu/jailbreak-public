"use client";

import { LoginForm } from "@/components/login-form";
import { useTeamLogin } from "@/lib/hooks/auth";

export default function TeamLoginPage() {
  const login = useTeamLogin();
  return (
    <LoginForm
      title="Jailbreak Challenge"
      description="Sign in with your shared team account."
      userLabel="Team name or ID"
      pending={login.isPending || login.isSuccess}
      error={login.error}
      onSubmit={(team, password) => login.mutate({ team, password })}
      switchHref="/admin/login"
      switchLabel="Organiser sign in"
    />
  );
}
