import {
  CheckCircle2,
  Circle,
  CircleDashed,
  CircleDot,
  Clock,
  KeyRound,
  Loader2,
  Lock,
  MinusCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui";
import { cn } from "@/lib/format";
import type { BotStatus, ConversationResult } from "@/lib/types";

const BOT: Record<BotStatus, { label: string; icon: LucideIcon; color: string }> = {
  locked: { label: "Locked", icon: Lock, color: "text-muted" },
  unattempted: { label: "Unattempted", icon: Circle, color: "text-muted" },
  attempted: { label: "Attempted", icon: CircleDot, color: "text-info" },
  solved: { label: "Solved", icon: CheckCircle2, color: "text-good" },
};

const RESULT: Record<
  ConversationResult,
  { label: string; icon: LucideIcon; color: string; spin?: boolean }
> = {
  not_submitted: { label: "Not submitted", icon: MinusCircle, color: "text-muted" },
  answer_bot: { label: "Answer box", icon: KeyRound, color: "text-muted" },
  pending: { label: "Pending", icon: Clock, color: "text-warn" },
  grading: { label: "Grading", icon: Loader2, color: "text-info", spin: true },
  passed: { label: "Passed", icon: CheckCircle2, color: "text-good" },
  failed: { label: "Failed", icon: XCircle, color: "text-bad" },
};

export function BotStatusBadge({ status }: { status: BotStatus }) {
  const { label, icon: Icon, color } = BOT[status];
  return (
    <Badge className={color}>
      <Icon className="size-3.5" aria-hidden />
      {label}
    </Badge>
  );
}

export function ResultBadge({ result }: { result: ConversationResult }) {
  const { label, icon: Icon, color, spin } = RESULT[result];
  return (
    <Badge className={color}>
      <Icon className={cn("size-3.5", spin && "animate-spin")} aria-hidden />
      {label}
    </Badge>
  );
}

export function EnabledBadge({ enabled, offLabel = "Disabled" }: { enabled: boolean; offLabel?: string }) {
  const Icon = enabled ? CheckCircle2 : CircleDashed;
  return (
    <Badge className={enabled ? "text-good" : "text-muted"}>
      <Icon className="size-3.5" aria-hidden />
      {enabled ? "Active" : offLabel}
    </Badge>
  );
}
