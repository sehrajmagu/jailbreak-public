"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";
import { thClass } from "@/components/ui";
import { cn } from "@/lib/format";

export function useSorted<T>(rows: T[] | undefined, initialKey: keyof T, initialDesc = true) {
  const [key, setKey] = useState(initialKey);
  const [desc, setDesc] = useState(initialDesc);

  const sorted = useMemo(() => {
    if (!rows) return undefined;
    return [...rows].sort((a, b) => {
      const [x, y] = [a[key], b[key]];
      // Rows with no value go last in either direction.
      if (x === null) return y === null ? 0 : 1;
      if (y === null) return -1;
      const order =
        typeof x === "number" && typeof y === "number"
          ? x - y
          : String(x).localeCompare(String(y));
      return desc ? -order : order;
    });
  }, [rows, key, desc]);

  function toggle(next: keyof T) {
    if (next === key) {
      setDesc(!desc);
    } else {
      setKey(next);
      setDesc(true);
    }
  }

  return { sorted, sortKey: key, desc, toggle };
}

export function SortHeader<T>({
  label,
  column,
  sort,
  numeric,
}: {
  label: string;
  column: keyof T;
  sort: { sortKey: keyof T; desc: boolean; toggle: (key: keyof T) => void };
  numeric?: boolean;
}) {
  const active = sort.sortKey === column;
  const Icon = sort.desc ? ArrowDown : ArrowUp;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.desc ? "descending" : "ascending") : "none"}
      className={cn(thClass, numeric && "text-right")}
    >
      <button
        type="button"
        onClick={() => sort.toggle(column)}
        className="inline-flex items-center gap-1 hover:text-foreground"
      >
        {label}
        {active && <Icon className="size-3" aria-hidden />}
      </button>
    </th>
  );
}
