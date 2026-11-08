"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Button, Card, Field, InlineError, Input } from "@/components/ui";

export function LoginForm({
  title,
  description,
  userLabel,
  pending,
  error,
  onSubmit,
  switchHref,
  switchLabel,
}: {
  title: string;
  description: string;
  userLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (user: string, password: string) => void;
  switchHref: string;
  switchLabel: string;
}) {
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(user, password);
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 mb-6 text-sm text-muted">{description}</p>
        <Card>
          <form onSubmit={submit} className="space-y-4">
            <Field label={userLabel}>
              {(id) => (
                <Input
                  id={id}
                  value={user}
                  onChange={(e) => setUser(e.target.value)}
                  autoComplete="username"
                  required
                  autoFocus
                />
              )}
            </Field>
            <Field label="Password">
              {(id) => (
                <Input
                  id={id}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                />
              )}
            </Field>
            <InlineError error={error} />
            <Button type="submit" className="w-full" disabled={pending}>
              {pending ? "Signing in..." : "Sign in"}
            </Button>
          </form>
        </Card>
        <p className="mt-4 text-center text-sm">
          <Link href={switchHref} className="text-muted underline underline-offset-4 hover:text-foreground">
            {switchLabel}
          </Link>
        </p>
      </div>
    </main>
  );
}
