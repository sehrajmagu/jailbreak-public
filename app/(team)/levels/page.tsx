"use client";

import Link from "next/link";
import { useState } from "react";
import { BotStatusBadge } from "@/components/status";
import { LevelProgressSummary } from "@/components/team/level-progress";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  PageHeader,
  Select,
  Skeleton,
} from "@/components/ui";
import { BOT_STATUSES, CHECK_TYPE_LABEL, LEVELS } from "@/lib/constants";
import { useLevels } from "@/lib/hooks/team";
import type { BotCard, BotStatus } from "@/lib/types";

function BotCardBody({ bot }: { bot: BotCard }) {
  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-medium">{bot.label}</h3>
          <p className="mt-0.5 text-sm text-muted">{bot.model}</p>
        </div>
        <BotStatusBadge status={bot.status} />
      </div>
      <div className="mt-4 flex items-center justify-between text-sm">
        <span className="text-muted">{CHECK_TYPE_LABEL[bot.checkType]}</span>
        <span className="font-medium">{bot.points} pts</span>
      </div>
    </>
  );
}

export default function LevelsPage() {
  const levels = useLevels();
  const [levelFilter, setLevelFilter] = useState("");
  const [status, setStatus] = useState<BotStatus | "">("");

  const visible = levels.data
    ?.filter((l) => !levelFilter || l.level === Number(levelFilter))
    .map((l) => ({ ...l, bots: l.bots.filter((b) => !status || b.status === status) }))
    .filter((l) => l.bots.length > 0);

  return (
    <>
      <PageHeader title="Levels" description="Each level has 6 bots. Solve enough of them to complete the level and win its prize." />

      <div className="mb-5 grid gap-3 sm:max-w-md sm:grid-cols-2">
        <Field label="Level">
          {(id) => (
            <Select id={id} value={levelFilter} onChange={(e) => setLevelFilter(e.target.value)}>
              <option value="">All levels</option>
              {LEVELS.map((l) => (
                <option key={l.level} value={l.level}>Level {l.level}: {l.name}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Status">
          {(id) => (
            <Select id={id} value={status} onChange={(e) => setStatus(e.target.value as BotStatus | "")}>
              <option value="">All statuses</option>
              {BOT_STATUSES.map((s) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </Select>
          )}
        </Field>
      </div>

      {levels.isError && !levels.data ? (
        <ErrorState error={levels.error} onRetry={() => levels.refetch()} />
      ) : !visible ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          title="No bots match these filters"
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setLevelFilter("");
                setStatus("");
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : (
        <div className="space-y-8">
          {visible.map((level) => (
            <section key={level.level} aria-labelledby={`level-${level.level}`}>
              <h2 id={`level-${level.level}`} className="mb-2 font-semibold">
                Level {level.level}: {level.name}
              </h2>
              <Card className="mb-3">
                <LevelProgressSummary level={level} />
              </Card>
              <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {level.bots.map((bot) => (
                  <li key={bot.id}>
                    {bot.status === "locked" ? (
                      <Card className="h-full opacity-70">
                        <BotCardBody bot={bot} />
                      </Card>
                    ) : (
                      <Link href={`/bots/${bot.id}`} className="block h-full rounded-lg">
                        <Card className="h-full transition hover:border-border-strong">
                          <BotCardBody bot={bot} />
                        </Card>
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}
