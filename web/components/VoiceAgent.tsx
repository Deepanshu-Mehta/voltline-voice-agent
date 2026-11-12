"use client";

import { useEffect, useRef, useState } from "react";
import { VoiceClient, type TurnMetrics, type VoiceState } from "@/lib/voice/client";

type Line =
  | { kind: "user"; text: string; final: boolean }
  | { kind: "agent"; turn: number; text: string; tools: string[]; interrupted: boolean; metrics: TurnMetrics | null };

const STATE_LABEL: Record<VoiceState, string> = {
  idle: "Not connected",
  connecting: "Connecting",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  error: "Stopped",
};

const TOOL_LABEL: Record<string, string> = {
  get_order_status: "Looked up the order",
  check_stock: "Checked stock",
  get_price: "Checked the price",
  book_callback: "Booked a callback",
  create_support_ticket: "Opened a support ticket",
};

function ms(v: number | null | undefined) {
  return v === null || v === undefined ? null : `${Math.round(v)} ms`;
}

export default function VoiceAgent() {
  const [state, setState] = useState<VoiceState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const client = useRef<VoiceClient | null>(null);
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [lines]);

  useEffect(() => () => void client.current?.stop(), []);

  function upsertAgent(turn: number, fn: (l: Extract<Line, { kind: "agent" }>) => Extract<Line, { kind: "agent" }>) {
    setLines((prev) => {
      const i = prev.findIndex((l) => l.kind === "agent" && l.turn === turn);
      if (i === -1) {
        const fresh = fn({ kind: "agent", turn, text: "", tools: [], interrupted: false, metrics: null });
        return [...prev, fresh];
      }
      const next = [...prev];
      next[i] = fn(prev[i] as Extract<Line, { kind: "agent" }>);
      return next;
    });
  }

  async function start() {
    setError(null);
    setLines([]);
    const c = new VoiceClient({
      onState: setState,
      onLevel: (l) => setLevel(Math.min(1, l * 6)),
      onError: setError,
      onUserText: (text, final) =>
        setLines((prev) => {
          const last = prev[prev.length - 1];
          if (last?.kind === "user" && !last.final) return [...prev.slice(0, -1), { kind: "user", text, final }];
          return [...prev, { kind: "user", text, final }];
        }),
      onAgentText: (turn, delta) => upsertAgent(turn, (l) => ({ ...l, text: l.text + delta })),
      onTool: (turn, name) => upsertAgent(turn, (l) => ({ ...l, tools: [...l.tools, name] })),
      onInterrupted: (turn) => upsertAgent(turn, (l) => ({ ...l, interrupted: true })),
      onMetrics: (m) => upsertAgent(m.turn, (l) => ({ ...l, metrics: { ...(l.metrics ?? { turn: m.turn }), ...m } })),
    });
    client.current = c;
    await c.start();
  }

  async function stop() {
    await client.current?.stop();
    client.current = null;
    setLevel(0);
  }

  const live = state !== "idle" && state !== "error";

  return (
    <section className="chat voice" aria-label="Talk to Volt by voice">
      <div className="chat-head">
        <span className="chat-state" aria-live="polite">
          <span className="lamp" data-on={live && state !== "connecting"} aria-hidden="true" />
          {STATE_LABEL[state]}
        </span>
        <span className="meter" aria-hidden="true">
          <span style={{ transform: `scaleX(${live ? level : 0})` }} />
        </span>
      </div>

      <div className="chat-log" ref={log}>
        {lines.length === 0 && (
          <div className="chat-empty voice-empty">
            <button
              type="button"
              className="call-button"
              onClick={live ? stop : start}
              disabled={state === "connecting"}
            >
              {state === "connecting" ? "Connecting" : "Start talking"}
            </button>
            <p>
              Ask about a product or say an order number, such as <strong>4512</strong>. You can interrupt Volt at any
              time. Headphones work best: on speakers, Volt may hear itself.
            </p>
            {error && <p className="error-line">{error}</p>}
          </div>
        )}
        {lines.map((l, i) =>
          l.kind === "user" ? (
            <div key={i} className="msg msg-user" data-final={l.final}>
              <p>{l.text}</p>
            </div>
          ) : (
            <div key={i} className="msg msg-agent">
              <div className="who">Volt</div>
              {l.tools.map((t, j) => (
                <div key={j} className="tool-note">
                  {TOOL_LABEL[t] ?? `Used ${t}`}
                </div>
              ))}
              <p>
                {l.text}
                {l.interrupted && <span className="cut"> (interrupted)</span>}
              </p>
              {l.metrics && (
                <div className="msg-meta" aria-label="Response timing">
                  {ms(l.metrics.client_response_ms ?? l.metrics.server_response_ms) && (
                    <span className="meta-strong">
                      Response {ms(l.metrics.client_response_ms ?? l.metrics.server_response_ms)}
                    </span>
                  )}
                  {ms(l.metrics.end_of_turn_ms) && <span>End of turn {ms(l.metrics.end_of_turn_ms)}</span>}
                  {ms(l.metrics.llm_first_token_ms) && <span>LLM first token {ms(l.metrics.llm_first_token_ms)}</span>}
                  {ms(l.metrics.tts_first_byte_ms) && <span>Voice first byte {ms(l.metrics.tts_first_byte_ms)}</span>}
                </div>
              )}
            </div>
          ),
        )}
      </div>

      {(lines.length > 0 || live) && (
        <div className="chat-form voice-controls">
          {error && lines.length > 0 && <p className="error-line">{error}</p>}
          <button type="button" className={live ? "button" : "button button-signal"} onClick={live ? stop : start}
            disabled={state === "connecting"}>
            {live ? "End call" : "Start a new call"}
          </button>
        </div>
      )}
    </section>
  );
}
