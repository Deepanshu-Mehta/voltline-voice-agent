"use client";

// "Ask Volt" on every page: a button that opens the chat in a side drawer. The drawer lives in
// the root layout, so the conversation carries on while the visitor browses. Any component can
// open it with a question by calling askVolt("...").
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Chat from "@/components/Chat";

const EVENT = "volt:ask";

export function askVolt(question?: string) {
  window.dispatchEvent(new CustomEvent(EVENT, { detail: question ?? null }));
}

export default function Launcher() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const drawer = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onAsk(e: Event) {
      const q = (e as CustomEvent<string | null>).detail;
      setOpen(true);
      if (q) setPending(q);
    }
    window.addEventListener(EVENT, onAsk);
    return () => window.removeEventListener(EVENT, onAsk);
  }, []);

  useEffect(() => {
    if (!open) return;
    drawer.current?.querySelector<HTMLInputElement>("input")?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  // The /talk page already shows the full chat.
  if (pathname === "/talk") return null;

  return (
    <>
      {!open && (
        <button ref={button} type="button" className="launcher" onClick={() => setOpen(true)} aria-haspopup="dialog">
          <span className="lamp" data-on="true" aria-hidden="true" />
          Ask Volt
        </button>
      )}
      <div
        ref={drawer}
        className="drawer"
        role="dialog"
        aria-modal="false"
        aria-label="Volt, the Voltline assistant"
        data-open={open}
        hidden={!open}
      >
        <Chat variant="panel" pending={pending} onPendingSent={() => setPending(null)} onClose={close} />
      </div>
    </>
  );
}
