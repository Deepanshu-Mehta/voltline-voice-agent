import type { Metadata } from "next";
import Link from "next/link";
import TalkPanel from "@/components/TalkPanel";

export const metadata: Metadata = { title: "Talk to us" };

export default function TalkPage() {
  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>Talk to Volt</h1>
        <p className="lede">
          Ask about products, delivery, returns or your order. Volt answers from this website and can check orders,
          stock and prices.
        </p>
      </header>
      <div className="talk-layout">
        <TalkPanel />
        <aside className="prose" aria-label="About this agent">
          <h2 style={{ marginTop: 0, fontSize: "1.25rem" }}>What Volt can do</h2>
          <ul>
            <li>Answer questions from the product pages, policies, guides and FAQ</li>
            <li>Check an order by its four-digit number</li>
            <li>Check stock and prices</li>
            <li>Open a support ticket or book a callback</li>
          </ul>
          <p>
            In a voice call, interrupt Volt whenever you like; it stops and listens. Each reply shows how long Volt
            took to start speaking.
          </p>
          <p>
            Sample order numbers are on the <Link href="/docs#demo-orders">Docs page</Link>. Phone, LiveKit and Vapi versions of Volt
            will appear on this page.
          </p>
        </aside>
      </div>
    </div>
  );
}
