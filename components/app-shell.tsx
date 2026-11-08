"use client";

import { LogOut, Moon, Sun, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Button, InlineError } from "@/components/ui";
import { cn } from "@/lib/format";
import { useLogout } from "@/lib/hooks/auth";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Match this path only, not its children. */
  exact?: boolean;
}

function ThemeToggle() {
  function toggle() {
    const dark = document.documentElement.classList.toggle("dark");
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      // The toggle still works for this page view without storage.
    }
  }
  return (
    <Button variant="ghost" onClick={toggle} aria-label="Toggle light or dark theme" className="px-2">
      <Sun className="hidden size-4 dark:block" aria-hidden />
      <Moon className="size-4 dark:hidden" aria-hidden />
    </Button>
  );
}

export function AppShell({
  title,
  nav,
  status,
  logoutTo,
  children,
}: {
  title: ReactNode;
  nav: NavItem[];
  status?: ReactNode;
  logoutTo: string;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const logout = useLogout(logoutTo);

  return (
    <div className="min-h-screen md:grid md:grid-cols-[240px_minmax(0,1fr)]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-surface focus:p-2"
      >
        Skip to content
      </a>
      <aside className="border-b border-border bg-surface md:sticky md:top-0 md:flex md:h-screen md:flex-col md:border-r md:border-b-0">
        <div className="flex items-center justify-between gap-2 px-4 pt-4 pb-2">
          <div className="min-w-0 truncate font-semibold">{title}</div>
          <div className="flex shrink-0 items-center">
            <ThemeToggle />
            <Button
              variant="ghost"
              className="px-2"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              aria-label="Log out"
              title="Log out"
            >
              <LogOut className="size-4" aria-hidden />
            </Button>
          </div>
        </div>
        {logout.error && (
          <div className="px-4">
            <InlineError error={logout.error} />
          </div>
        )}
        {status && <div className="px-4 py-2">{status}</div>}
        <nav
          aria-label="Main"
          className="flex gap-1 overflow-x-auto px-2 pb-2 md:mt-2 md:flex-col md:overflow-visible"
        >
          {nav.map(({ href, label, icon: Icon, exact }) => {
            const active = exact ? pathname === href : pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm whitespace-nowrap",
                  active ? "bg-subtle font-medium" : "text-muted hover:bg-subtle hover:text-foreground",
                )}
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <main id="main" className="min-w-0 px-4 py-6 md:px-8 md:py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
