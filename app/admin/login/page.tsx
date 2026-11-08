"use client";

import { LoginForm } from "@/components/login-form";
import { useAdminLogin } from "@/lib/hooks/auth";

export default function AdminLoginPage() {
  const login = useAdminLogin();
  return (
    <LoginForm
      title="Organiser sign in"
      description="For event admins only."
      userLabel="Username"
      pending={login.isPending || login.isSuccess}
      error={login.error}
      onSubmit={(username, password) => login.mutate({ username, password })}
      switchHref="/login"
      switchLabel="Team sign in"
    />
  );
}
