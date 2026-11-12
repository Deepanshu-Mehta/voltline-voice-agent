import { Suspense } from "react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import AskButton from "@/components/AskButton";
import ProductArt from "@/components/ProductArt";
import ProductCard, { stockClass } from "@/components/ProductCard";
import { getProduct, getProducts, price, slugify, stockLabel, toCard } from "@/lib/content";

export function generateStaticParams() {
  return getProducts().map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: PageProps<"/products/[slug]">): Promise<Metadata> {
  const p = getProduct((await params).slug);
  return { title: p ? `${p.name} (${p.sku})` : "Product", description: p?.tagline };
}

// URL data is read inside Suspense so client navigation can show the page shell at once.
export default function ProductPage({ params }: PageProps<"/products/[slug]">) {
  return (
    <Suspense fallback={<div className="wrap page" />}>
      <ProductPageBody params={params} />
    </Suspense>
  );
}

async function ProductPageBody({ params }: { params: PageProps<"/products/[slug]">["params"] }) {
  const p = getProduct((await params).slug);
  if (!p) notFound();
  const related = getProducts().filter((o) => o.category === p.category && o.sku !== p.sku);
  const carePlus = p.price < 100 ? 19 : 29;

  return (
    <div className="wrap page">
      <nav aria-label="Breadcrumb" className="crumb">
        <Link href="/products">Products</Link> / <Link href={`/products#${slugify(p.category)}`}>{p.category}</Link>
      </nav>
      <div className="product-layout">
        <div>
          <header className="page-head">
            <h1>{p.name}</h1>
            <p className="lede">{p.tagline}</p>
          </header>
          <div className="art-well art-hero">
            <ProductArt sku={p.sku} name={p.name} />
          </div>
          <article className="prose" dangerouslySetInnerHTML={{ __html: p.html }} />
        </div>

        <aside className="product-aside" aria-label="Product summary">
          <div className="plate">
            <div className="model">
              <span>MODEL {p.sku}</span>
              <span>{p.warrantyMonths} MO</span>
            </div>
            <p className="big-price">{price(p.price)}</p>
            <p className={stockClass(p)} style={{ margin: "8px 0 14px" }}>
              {stockLabel(p)}
            </p>
            <dl>
              <dt>Range</dt>
              <dd>{p.category}</dd>
              <dt>Warranty</dt>
              <dd>{p.warrantyMonths} months</dd>
              <dt>Colors</dt>
              <dd>{p.colors.join(", ")}</dd>
              <dt>Delivery</dt>
              <dd>{p.price >= 50 ? "Free standard delivery" : "Free standard delivery on orders of $50 or more"}</dd>
              <dt>Care+</dt>
              <dd>${carePlus} for 2 extra years</dd>
            </dl>
          </div>
          <AskButton question={`Tell me about the ${p.name}.`} />
          <p className="aside-note">
            30-day returns. <Link href="/returns-warranty">Returns and warranty</Link>
          </p>
        </aside>
      </div>

      {related.length > 0 && (
        <section className="category" aria-labelledby="related">
          <h2 id="related">More from {p.category}</h2>
          <ul className="grid">
            {related.map((o) => (
              <li key={o.sku}>
                <ProductCard product={toCard(o)} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
