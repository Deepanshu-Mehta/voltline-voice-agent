import type { Metadata } from "next";
import LatencyTable from "@/components/LatencyTable";
import { getLatestEval } from "@/lib/content";

export const metadata: Metadata = { title: "Benchmarks" };

const ROWS: [string, string, string][] = [
  ["correctness_pct", "Factual correctness (LLM judge)", "%"],
  ["hallucination_pct", "Hallucination rate", "%"],
  ["out_of_scope_handled", "Out-of-scope questions handled", ""],
  ["retrieval_hit3_pct", "Retrieval hit rate, top 3", "%"],
  ["keyword_pass_pct", "Key facts present in the reply", "%"],
  ["tool_selection_pct", "Right tool chosen", "%"],
  ["tool_args_pct", "Right tool arguments", "%"],
  ["median_words", "Median reply length", " words"],
  ["format_violation_items", "Replies with lists, markdown or links", ""],
  ["median_retrieval_ms", "Median retrieval time", " ms"],
  ["median_llm_first_token_ms", "Median time to first LLM token", " ms"],
  ["median_total_ms", "Median total text turn time", " ms"],
];

export default function BenchmarksPage() {
  const report = getLatestEval();
  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>Benchmarks</h1>
        <p className="lede">
          How accurate and how fast the agent is. Voice latency across all channels is added here as each channel is
          built.
        </p>
      </header>

      <h2>Live latency</h2>
      <LatencyTable />

      <h2>Text agent evaluation</h2>
      {report ? (
        <>
          <p className="prose">
            {String(report.summary.n)} test questions run through the text agent on{" "}
            {new Date(report.timestamp).toLocaleString("en-US", { dateStyle: "long", timeStyle: "short" })}
            {report.model ? `, model ${report.model}` : ""}
            {report.judge_model ? `, judged by ${report.judge_model}` : ", without the LLM judge"}.
          </p>
          <table className="table metric-table" style={{ maxWidth: 640 }}>
            <thead>
              <tr>
                <th scope="col">Metric</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.filter(([k]) => report.summary[k] !== undefined && report.summary[k] !== null).map(([k, label, unit]) => (
                <tr key={k}>
                  <td>{label}</td>
                  <td>
                    {String(report.summary[k])}
                    {unit}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="prose">No evaluation results yet. Run <code>make eval-text</code> and rebuild the site.</p>
      )}
    </div>
  );
}
