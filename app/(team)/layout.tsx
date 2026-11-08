"use client";

import { FileCheck2, Home, ListOrdered, Trophy } from "lucide-react";
import type { ReactNode } from "react";
import { AppShell, type NavItem } from "@/components/app-shell";
import { Skeleton } from "@/components/ui";
import { formatNumber, formatUsd } from "@/lib/format";
import { useBudget, useMe } from "@/lib/hooks/team";

const NAV: NavItem[] = [
  { href: "/dashboard", label: "Home", icon: Home },
  { href: "/levels", label: "Levels", icon: ListOrdered },
  { href: "/submissions", label: "Submissions", icon: FileCheck2 },
  { href: "/leaderboard", label: "Leaderboard", icon: Trophy },
];

function Balance() {
  const { data: me } = useMe();
  const budget = useBudget();
  if (!me) return <Skeleton className="h-12 w-full" />;
  return (
    <dl className="rounded-md bg-subtle p-3 text-sm">
      <dt className="text-xs text-muted">AI budget left</dt>
      <dd className="mb-2 font-semibold">
        {budget.data
          ? `${formatUsd(budget.data.remainingUsd)} of ${formatUsd(budget.data.budgetUsd)}`
          : budget.isError
            ? "Unavailable"
            : "-"}
      </dd>
      <dt className="text-xs text-muted">Tokens used, all models</dt>
      <dd className="font-semibold">{formatNumber(me.tokensUsed)}</dd>
    </dl>
  );
}

function TeamName() {
  const { data: me } = useMe();
  return me ? <span>{me.name}</span> : <Skeleton className="h-5 w-28" />;
}

export default function TeamLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell title={<TeamName />} nav={NAV} status={<Balance />} logoutTo="/login">
      {children}
    </AppShell>
  );
}
