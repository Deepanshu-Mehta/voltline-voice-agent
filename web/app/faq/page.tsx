import type { Metadata } from "next";
import AskButton from "@/components/AskButton";
import { getFaq } from "@/lib/content";

export const metadata: Metadata = { title: "FAQ" };

export default function FaqPage() {
  const sections = getFaq();
  const total = sections.reduce((n, s) => n + s.items.length, 0);
  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>Frequently asked questions</h1>
        <p className="lede">{total} answers on orders, returns, warranty, products and payments.</p>
      </header>
      <div className="with-sidebar">
        <nav className="sidebar" aria-label="FAQ sections">
          <ul>
            {sections.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`}>
                  {s.title}
                  <span>{s.items.length}</span>
                </a>
              </li>
            ))}
          </ul>
          <AskButton question="" label="Ask Volt instead" className="button" />
        </nav>
        <div>
          {sections.map((s) => (
            <section key={s.id} id={s.id} className="faq-section" aria-labelledby={`h-${s.id}`}>
              <h2 id={`h-${s.id}`}>{s.title}</h2>
              {s.items.map((item) => (
                <details key={item.id} id={item.id} className="faq-item">
                  <summary>{item.q}</summary>
                  <div className="prose" dangerouslySetInnerHTML={{ __html: item.html }} />
                </details>
              ))}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
