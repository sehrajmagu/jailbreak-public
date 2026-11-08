"use client";

import { BarChart3, FileCheck2, IndianRupee, LayoutDashboard, MessagesSquare, Settings2, Users } from "lucide-react";
import type { ReactNode } from "react";
import { AppShell, type NavItem } from "@/components/app-shell";

const NAV: NavItem[] = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/admin/teams", label: "Teams", icon: Users },
  { href: "/admin/conversations", label: "Conversations", icon: MessagesSquare },
  { href: "/admin/submissions", label: "Submissions", icon: FileCheck2 },
  { href: "/admin/prizes", label: "Prizes", icon: IndianRupee },
  { href: "/admin/bot-stats", label: "Bot stats", icon: BarChart3 },
  { href: "/admin/bots", label: "Manage bots", icon: Settings2 },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell title="Admin" nav={NAV} logoutTo="/admin/login">
      {children}
    </AppShell>
  );
}
