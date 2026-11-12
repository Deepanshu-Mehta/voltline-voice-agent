"use client";

import { askVolt } from "@/components/Launcher";

// Opens the Volt drawer with a question already asked.
export default function AskButton({ question, label = "Ask Volt about this", className }: {
  question: string;
  label?: string;
  className?: string;
}) {
  return (
    <button type="button" className={className ?? "button button-signal"} onClick={() => askVolt(question)}>
      {label}
    </button>
  );
}
