"use client";

import { useMemo, useState } from "react";
import ProductCard, { type CardProduct } from "@/components/ProductCard";

type Sort = "range" | "price-asc" | "price-desc";

function slug(text: string) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

export default function ProductBrowser({ products, categories }: { products: CardProduct[]; categories: string[] }) {
  const [range, setRange] = useState<string>("All");
  const [inStock, setInStock] = useState(false);
  const [sort, setSort] = useState<Sort>("range");

  const shown = useMemo(() => {
    const list = products.filter((p) => (range === "All" || p.category === range) && (!inStock || p.stock > 0));
    if (sort === "price-asc") return [...list].sort((a, b) => a.price - b.price);
    if (sort === "price-desc") return [...list].sort((a, b) => b.price - a.price);
    return list;
  }, [products, range, inStock, sort]);

  const grouped = sort === "range" && range === "All";

  return (
    <>
      <div className="toolbar" role="group" aria-label="Filter and sort products">
        <div className="segmented" role="radiogroup" aria-label="Range">
          {["All", ...categories].map((c) => (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={range === c}
              className="segment"
              onClick={() => setRange(c)}
            >
              {c}
            </button>
          ))}
        </div>
        <div className="toolbar-right">
          <label className="check">
            <input type="checkbox" checked={inStock} onChange={(e) => setInStock(e.target.checked)} />
            In stock only
          </label>
          <label className="select">
            <span>Sort</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="range">By range</option>
              <option value="price-asc">Price, low to high</option>
              <option value="price-desc">Price, high to low</option>
            </select>
          </label>
        </div>
      </div>

      <p className="result-count" aria-live="polite">
        {shown.length} {shown.length === 1 ? "product" : "products"}
      </p>

      {grouped ? (
        categories.map((c) => {
          const items = shown.filter((p) => p.category === c);
          if (!items.length) return null;
          return (
            <section key={c} className="category" id={slug(c)} aria-labelledby={`h-${slug(c)}`}>
              <h2 id={`h-${slug(c)}`}>{c}</h2>
              <ul className="grid">
                {items.map((p) => (
                  <li key={p.sku}>
                    <ProductCard product={p} />
                  </li>
                ))}
              </ul>
            </section>
          );
        })
      ) : shown.length ? (
        <ul className="grid category">
          {shown.map((p) => (
            <li key={p.sku}>
              <ProductCard product={p} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty">
          Nothing in {range} is in stock right now.{" "}
          <button type="button" className="link-button" onClick={() => setInStock(false)}>
            Show out-of-stock products
          </button>
        </p>
      )}
    </>
  );
}
