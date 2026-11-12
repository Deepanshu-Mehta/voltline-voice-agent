"use client";

// Live latency per channel and mode, read from the API's turns table.
import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

type Stat = { median: number; p95: number; n: number };
type Group = {
  channel: string;
  mode: string;
  turns: number;
  response_ms?: Stat;
  end_of_turn_ms?: Stat;
  llm_first_token_ms?: Stat;
  tts_first_byte_ms?: Stat;
  retrieval_ms?: Stat;
};

const CHANNEL_LABEL: Record<string, string> = {
  web: "Website voice",
  "web-text": "Website text chat",
  text: "Text API",
};

const COLUMNS: [keyof Group, string][] = [
  ["response_ms", "Response"],
  ["end_of_turn_ms", "End of turn"],
  ["llm_first_token_ms", "LLM first token"],
  ["tts_first_byte_ms", "Voice first byte"],
  ["retrieval_ms", "Retrieval"],
];

function cell(s?: Stat) {
  if (!s) return "–";
  return `${Math.round(s.median)} / ${Math.round(s.p95)}`;
}

export default function LatencyTable() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch(`${API_URL}/turns/summary`)
        .then((r) => r.json())
        .then((g: Group[]) => alive && (setGroups(g), setError(false)))
        .catch(() => alive && setError(true));
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (error) return <p className="prose">The API is offline, so live latency cannot be shown.</p>;
  if (!groups) return <p className="prose">Loading latency from the API.</p>;
  if (!groups.length)
    return <p className="prose">No turns logged in the last 7 days. Talk to Volt and this table fills in.</p>;

  return (
    <>
      <table className="table metric-table">
        <thead>
          <tr>
            <th scope="col">Channel</th>
            <th scope="col">Mode</th>
            <th scope="col">Turns</th>
            {COLUMNS.map(([, label]) => (
              <th key={label} scope="col">{label}, ms</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={`${g.channel}-${g.mode}`}>
              <td>{CHANNEL_LABEL[g.channel] ?? g.channel}</td>
              <td>{g.mode}</td>
              <td>{g.turns}</td>
              {COLUMNS.map(([key]) => (
                <td key={key}>{cell(g[key] as Stat | undefined)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="prose table-note">
        Each cell shows the median, then the 95th percentile, over the last 7 days. Response is measured in the browser
        from the end of your speech to the first sound of the reply; for text it is the time to the full reply.
      </p>
    </>
  );
}
