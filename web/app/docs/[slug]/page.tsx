import { Suspense } from "react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import AskButton from "@/components/AskButton";
import DocsSidebar from "@/components/DocsSidebar";
import { getDocs } from "@/lib/content";

export function generateStaticParams() {
  return getDocs().map((d) => ({ slug: d.slug }));
}

export async function generateMetadata({ params }: PageProps<"/docs/[slug]">): Promise<Metadata> {
  const { slug } = await params;
  return { title: getDocs().find((d) => d.slug === slug)?.title ?? "Docs" };
}

// URL data is read inside Suspense so client navigation can show the page shell at once.
export default function DocPage({ params }: PageProps<"/docs/[slug]">) {
  return (
    <Suspense fallback={<div className="wrap page" />}>
      <DocPageBody params={params} />
    </Suspense>
  );
}

async function DocPageBody({ params }: { params: PageProps<"/docs/[slug]">["params"] }) {
  const { slug } = await params;
  const doc = getDocs().find((d) => d.slug === slug);
  if (!doc) notFound();
  return (
    <div className="wrap page">
      <nav aria-label="Breadcrumb" className="crumb">
        <Link href="/docs">Docs</Link> / Setup guide
      </nav>
      <div className="with-sidebar">
        <DocsSidebar current={doc.slug} />
        <div>
          <header className="page-head">
            <h1 className="h1-compact">{doc.title}</h1>
          </header>
          <article className="prose" dangerouslySetInnerHTML={{ __html: doc.html }} />
          <div className="help-box plate">
            <p>Stuck on a step? Volt can walk you through it.</p>
            <AskButton question={`I need help with the ${doc.title} guide.`} label="Ask Volt for help" />
          </div>
        </div>
      </div>
    </div>
  );
}
