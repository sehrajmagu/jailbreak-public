"use client";

import Link from "next/link";
import { EnabledBadge } from "@/components/status";
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  LoadingRows,
  PageHeader,
  TableWrap,
  tdClass,
  thClass,
} from "@/components/ui";
import { CHECK_TYPE_LABEL } from "@/lib/constants";
import { cn } from "@/lib/format";
import { useAdminBots, useSetBotEnabled } from "@/lib/hooks/admin";

export default function ManageBotsPage() {
  const bots = useAdminBots();
  const setEnabled = useSetBotEnabled();

  return (
    <>
      <PageHeader
        title="Manage bots and models"
        description="Disabled bots are hidden from teams. Bots are edited in their files."
      />
      <div className="mb-3">
        <InlineError error={setEnabled.error} />
      </div>
      {bots.isError && !bots.data ? (
        <ErrorState error={bots.error} onRetry={() => bots.refetch()} />
      ) : !bots.data ? (
        <LoadingRows rows={12} />
      ) : bots.data.length === 0 ? (
        <EmptyState title="No bots yet" />
      ) : (
        <TableWrap>
          <caption className="sr-only">All bots</caption>
          <thead>
            <tr>
              <th scope="col" className={thClass}>Level</th>
              <th scope="col" className={thClass}>Bot</th>
              <th scope="col" className={thClass}>Model</th>
              <th scope="col" className={thClass}>Check</th>
              <th scope="col" className={cn(thClass, "text-right")}>Points</th>
              <th scope="col" className={thClass}>Status</th>
              <th scope="col" className={thClass}><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {bots.data.map((b) => (
              <tr key={b.id}>
                <td className={tdClass}>{b.level}</td>
                <td className={cn(tdClass, "whitespace-nowrap")}>
                  <Link href={`/admin/bots/${b.id}`} className="font-medium underline-offset-4 hover:underline">
                    {b.label}
                  </Link>
                </td>
                <td className={cn(tdClass, "whitespace-nowrap")}>{b.model}</td>
                <td className={cn(tdClass, "whitespace-nowrap")}>{CHECK_TYPE_LABEL[b.checkType]}</td>
                <td className={cn(tdClass, "text-right")}>{b.points}</td>
                <td className={tdClass}><EnabledBadge enabled={b.enabled} /></td>
                <td className={tdClass}>
                  <Button
                    variant="ghost"
                    disabled={setEnabled.isPending}
                    aria-label={`${b.enabled ? "Disable" : "Enable"} level ${b.level} ${b.label}`}
                    onClick={() => setEnabled.mutate({ id: b.id, enabled: !b.enabled })}
                  >
                    {b.enabled ? "Disable" : "Enable"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </>
  );
}
