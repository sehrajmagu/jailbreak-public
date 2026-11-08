"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Button, buttonClass, Card, Field, InlineError, Input, Select, Textarea } from "@/components/ui";
import { CHECK_TYPE_LABEL, LEVELS, MODELS } from "@/lib/constants";
import type { BotInput, CheckType, ModelName } from "@/lib/types";

export function BotForm({
  initial,
  submitLabel,
  pending,
  error,
  saved,
  onSubmit,
  readOnly,
}: {
  initial: BotInput;
  submitLabel?: string;
  pending?: boolean;
  error?: unknown;
  saved?: boolean;
  onSubmit?: (input: BotInput) => void;
  /** Shows the bot without letting it be changed. */
  readOnly?: boolean;
}) {
  const [value, setValue] = useState(initial);
  const [defences, setDefences] = useState(initial.defences.join("\n"));
  const set = <K extends keyof BotInput>(key: K, next: BotInput[K]) =>
    setValue((current) => ({ ...current, [key]: next }));

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit?.({
      ...value,
      defences: defences.split("\n").map((d) => d.trim()).filter(Boolean),
    });
  }

  return (
    <form onSubmit={submit} className="max-w-3xl space-y-4">
      <fieldset disabled={readOnly} className="space-y-4">
      <Card className="space-y-3">
        <h2 className="font-medium">Shown to teams</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Label">
            {(id) => <Input id={id} value={value.label} onChange={(e) => set("label", e.target.value)} required />}
          </Field>
          <Field label="Level">
            {(id) => (
              <Select id={id} value={value.level} onChange={(e) => set("level", Number(e.target.value))}>
                {LEVELS.map((l) => <option key={l.level} value={l.level}>Level {l.level}: {l.name}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Check type">
            {(id) => (
              <Select id={id} value={value.checkType} onChange={(e) => set("checkType", e.target.value as CheckType)}>
                {(Object.keys(CHECK_TYPE_LABEL) as CheckType[]).map((c) => (
                  <option key={c} value={c}>{CHECK_TYPE_LABEL[c]}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Model">
            {(id) => (
              <Select id={id} value={value.model} onChange={(e) => set("model", e.target.value as ModelName)}>
                {/* Real model names aren't in MODELS until it's updated. */}
                {!MODELS.includes(value.model) && <option value={value.model}>{value.model}</option>}
                {MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Points">
            {(id) => <Input id={id} type="number" min={1} value={value.points} onChange={(e) => set("points", Number(e.target.value))} required />}
          </Field>
        </div>
        <Field label="Description">
          {(id) => <Textarea id={id} rows={3} value={value.description} onChange={(e) => set("description", e.target.value)} required />}
        </Field>
        <Field label="Goal">
          {(id) => <Textarea id={id} rows={2} value={value.goal} onChange={(e) => set("goal", e.target.value)} required />}
        </Field>
      </Card>

      <Card className="space-y-3">
        <h2 className="font-medium">Admin only</h2>
        <p className="text-sm text-muted">Teams never see these fields.</p>
        <Field label="Hidden system prompt">
          {(id) => <Textarea id={id} rows={5} className="font-mono" value={value.systemPrompt} onChange={(e) => set("systemPrompt", e.target.value)} required />}
        </Field>
        {value.checkType === "answer" ? (
          <Field label="Password">
            {(id) => <Input id={id} className="font-mono" value={value.secret} onChange={(e) => set("secret", e.target.value)} required />}
          </Field>
        ) : (
          <Field label="Win rubric">
            {(id) => <Textarea id={id} rows={3} value={value.winRubric} onChange={(e) => set("winRubric", e.target.value)} required />}
          </Field>
        )}
        <Field label="Defences" hint="One per line.">
          {(id) => <Textarea id={id} rows={4} value={defences} onChange={(e) => setDefences(e.target.value)} />}
        </Field>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
            className="size-4"
          />
          Enabled
        </label>
      </Card>
      </fieldset>

      <InlineError error={error} />
      <div className="flex items-center gap-2">
        {!readOnly && (
          <Button type="submit" disabled={pending}>{pending ? "Saving..." : submitLabel}</Button>
        )}
        <Link href="/admin/bots" className={buttonClass("secondary")}>Back</Link>
        {saved && <span role="status" className="text-sm text-good">Saved</span>}
      </div>
    </form>
  );
}
