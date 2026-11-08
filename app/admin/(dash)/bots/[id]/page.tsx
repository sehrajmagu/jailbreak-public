"use client";

import { useParams } from "next/navigation";
import { BotForm } from "@/components/admin/bot-form";
import { ErrorState, LoadingRows, PageHeader } from "@/components/ui";
import { useAdminBot } from "@/lib/hooks/admin";

export default function BotPage() {
  const { id } = useParams<{ id: string }>();
  const bot = useAdminBot(id);

  if (bot.isError && !bot.data) {
    return <ErrorState error={bot.error} onRetry={() => bot.refetch()} />;
  }
  if (!bot.data) return <LoadingRows rows={8} />;

  return (
    <>
      <PageHeader
        title={`Level ${bot.data.level} ${bot.data.label}`}
        description="Bots are edited in their files, not here."
      />
      <BotForm key={bot.data.id} initial={bot.data} readOnly />
    </>
  );
}
