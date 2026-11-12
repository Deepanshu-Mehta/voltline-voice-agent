import Link from "next/link";
import { getPage } from "@/lib/content";

// A content page rendered from content/<slug>.md
export default function ProsePage({ slug, lede }: { slug: string; lede?: string }) {
  const page = getPage(slug);
  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>{page.title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </header>
      <article className="prose" dangerouslySetInnerHTML={{ __html: page.html }} />
      <p className="prose" style={{ marginTop: 40 }}>
        Still have a question? <Link href="/talk">Ask Volt, our voice agent</Link>.
      </p>
    </div>
  );
}
