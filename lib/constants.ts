import type { BotStatus, CheckType, ModelName } from "@/lib/types";

export const LEVELS: { level: number; name: string }[] = [
  { level: 1, name: "Password" },
  { level: 2, name: "Forbidden topic" },
  { level: 3, name: "Harmful language in Hindi" },
  { level: 4, name: "Malware" },
  { level: 5, name: "Explicit content" },
];

export const MODELS: ModelName[] = [
  "Astra",
  "Terra",
  "Sol",
  "Luna",
  "Open-source LLM",
  "Fable 5.1",
];

export const BOT_LABELS = ["Bot A", "Bot B", "Bot C", "Bot D", "Bot E", "Bot F"];

export const BOT_STATUSES: { id: BotStatus; label: string }[] = [
  { id: "locked", label: "Locked" },
  { id: "unattempted", label: "Unattempted" },
  { id: "attempted", label: "Attempted" },
  { id: "solved", label: "Solved" },
];

export const CHECK_TYPE_LABEL: Record<CheckType, string> = {
  answer: "Answer box",
  judge: "Judge",
};
