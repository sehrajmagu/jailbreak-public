import { CheckCircle2, Lock } from "lucide-react";
import { Badge } from "@/components/ui";
import { formatRupees } from "@/lib/format";
import type { LevelProgress } from "@/lib/types";

export function LevelProgressSummary({ level }: { level: LevelProgress }) {
  const counted = Math.min(level.solved, level.required);
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        {level.complete ? (
          <Badge className="text-good">
            <CheckCircle2 className="size-3.5" aria-hidden /> Level complete
          </Badge>
        ) : level.locked ? (
          <Badge className="text-muted">
            <Lock className="size-3.5" aria-hidden /> Complete the previous level to unlock
          </Badge>
        ) : level.revoked ? (
          <span className="font-medium text-bad">Completion revoked by the organisers</span>
        ) : (
          <span className="font-medium">
            {counted} of {level.required} needed
          </span>
        )}
        <span className="text-muted">
          {level.complete
            ? `${level.solved} of ${level.total} solved · Prize: ${formatRupees(level.prizeAwarded)}`
            : `Prize: ${formatRupees(level.prizeAmount)}`}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`Bots needed for level ${level.level}`}
        aria-valuemin={0}
        aria-valuemax={level.required}
        aria-valuenow={counted}
        className="mt-2 h-2 overflow-hidden rounded-full bg-subtle"
      >
        <div
          className={level.complete ? "h-full rounded-full bg-good" : "h-full rounded-full bg-primary"}
          style={{ width: `${(counted / Math.max(1, level.required)) * 100}%` }}
        />
      </div>
    </div>
  );
}
