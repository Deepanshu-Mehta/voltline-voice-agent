"use client";

// Text chat with the agent brain over POST /chat (Server-Sent Events). Phase 2 adds voice to
// this panel; the brain, the tools and the knowledge base stay the same.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { API_URL } from "@/lib/api";

type ToolCall = { name: string; args: Record<string, unknown> };
type Timings = { retrieval_ms?: number; llm_first_token_ms?: number; total_ms?: number };
type Msg = {
  role: "user" | "assistant";
  content: string;
  tools?: ToolCall[];
  timings?: Timings;
  error?: string | null;
};

const SAMPLES = [
  "How much are the Pulse Buds Pro?",
  "Where is order 4512?",
  "Are the Horizon Headphones in stock?",
  "Can I return opened earbuds?",
];

const TOOL_LABEL: Record<string, (a: Record<string, unknown>) => string> = {
  get_order_status: (a) => `Looked up order ${a.order_id ?? ""}`,
  check_stock: (a) => `Checked stock for ${a.product ?? "a product"}`,
  get_price: (a) => `Checked the price of ${a.product ?? "a product"}`,
  book_callback: () => "Booked a callback",
  create_support_ticket: () => "Opened a support ticket",
};

function newSession() {
  return Math.random().toString(36).slice(2, 10);
}

type ChatProps = {
  /** Send ?q=... from the page URL on arrival (the /talk page). */
  autoFromUrl?: boolean;
  /** A question to send as soon as Volt is free (the launcher's "Ask Volt about this"). */
  pending?: string | null;
  onPendingSent?: () => void;
  /** "page" draws the bordered plate; "panel" fills the launcher drawer. */
  variant?: "page" | "panel";
  onClose?: () => void;
};

export default function Chat({ autoFromUrl = false, pending = null, onPendingSent, variant = "page", onClose }: ChatProps) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const session = useRef(""); // set on first send; random values cannot run during prerender
  const log = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [messages]);

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    if (!session.current) session.current = newSession();
    setInput("");
    setBusy(true);
    setOffline(false);
    const history: Msg[] = [...messages, { role: "user", content: question }];
    setMessages([...history, { role: "assistant", content: "", tools: [] }]);

    const update = (fn: (m: Msg) => Msg) =>
      setMessages((prev) => [...prev.slice(0, -1), fn(prev[prev.length - 1])]);

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: history.map(({ role, content }) => ({ role, content })),
          session_id: session.current,
          channel: "web-text",
        }),
      });
      if (!res.ok || !res.body) throw new Error(`The agent returned ${res.status}.`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const raw of events) {
          if (!raw.startsWith("data: ")) continue;
          const e = JSON.parse(raw.slice(6));
          if (e.type === "token") update((m) => ({ ...m, content: m.content + e.text }));
          if (e.type === "tool_call")
            update((m) => ({ ...m, tools: [...(m.tools ?? []), { name: e.name, args: e.args }] }));
          if (e.type === "done") update((m) => ({ ...m, content: e.reply, timings: e.timings, error: e.error }));
        }
      }
    } catch {
      setOffline(true);
      update((m) => ({ ...m, content: "", error: "offline" }));
    } finally {
      setBusy(false);
    }
  }

  // Questions sent from the home page arrive as /talk?q=... Deferred so it runs once, after
  // mount; the ref stops a resend when Next.js restores this page from navigation history.
  useEffect(() => {
    if (!autoFromUrl) return;
    const q = new URLSearchParams(window.location.search).get("q");
    if (!q || started.current) return;
    const timer = setTimeout(() => {
      started.current = true;
      void send(q);
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!pending || busy) return;
    const timer = setTimeout(() => {
      onPendingSent?.();
      void send(pending);
    }, 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, busy]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void send(input);
  }

  function reset() {
    setMessages([]);
    session.current = "";
  }

  return (
    <section className={variant === "panel" ? "chat chat-panel" : "chat"} aria-label="Chat with Volt">
      <div className="chat-head">
        <span className="chat-state">
          <span className="lamp" data-on={busy} aria-hidden="true" />
          {busy ? "Volt is answering" : "Volt is ready"}
        </span>
        <span className="chat-actions">
          <button type="button" className="chip" onClick={reset} disabled={busy || messages.length === 0}>
            New conversation
          </button>
          {onClose && (
            <button type="button" className="chip" onClick={onClose} aria-label="Close Volt">
              Close
            </button>
          )}
        </span>
      </div>

      <div className="chat-log" ref={log} aria-live="polite">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>Ask about a product, a policy or your order. Try one of these:</p>
            <ul className="ask-hints">
              {SAMPLES.map((s) => (
                <li key={s}>
                  <button type="button" className="chip" onClick={() => send(s)}>
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg msg-user">
              <p>{m.content}</p>
            </div>
          ) : (
            <div key={i} className="msg msg-agent">
              <div className="who">Volt</div>
              {m.tools?.map((t, j) => (
                <div key={j} className="tool-note">
                  {(TOOL_LABEL[t.name] ?? (() => `Used ${t.name}`))(t.args)}
                </div>
              ))}
              {m.error === "offline" ? (
                <p className="error-line">
                  Volt can&apos;t be reached. Start the server with <code>make server</code>, then try again.
                </p>
              ) : (
                <p>{m.content || (busy && i === messages.length - 1 ? "…" : "")}</p>
              )}
              {m.timings?.total_ms !== undefined && (
                <div className="msg-meta" aria-label="Response timing">
                  <span>Retrieval {Math.round(m.timings.retrieval_ms ?? 0)} ms</span>
                  {m.timings.llm_first_token_ms !== undefined && (
                    <span>First token {Math.round(m.timings.llm_first_token_ms)} ms</span>
                  )}
                  <span>Total {Math.round(m.timings.total_ms)} ms</span>
                </div>
              )}
            </div>
          ),
        )}
      </div>

      <form className="chat-form" onSubmit={onSubmit}>
        <label htmlFor="chat-input" className="skip">
          Your message
        </label>
        <input
          id="chat-input"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={offline ? "Volt is offline" : "Type your question"}
          autoComplete="off"
          maxLength={1000}
        />
        <button className="button button-signal" type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
