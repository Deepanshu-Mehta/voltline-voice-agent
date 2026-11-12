import Link from "next/link";
import ProductArt from "@/components/ProductArt";
import { price, stockLabel } from "@/lib/format";

// Only the fields a card needs, so client components can receive cards cheaply.
export type CardProduct = {
  sku: string;
  name: string;
  slug: string;
  category: string;
  price: number;
  stock: number;
  restockDate?: string;
  warrantyMonths: number;
  tagline: string;
};

export function stockClass(p: { stock: number }) {
  if (p.stock === 0) return "status status-out";
  return p.stock < 10 ? "status status-low" : "status status-ok";
}

export default function ProductCard({ product: p }: { product: CardProduct }) {
  return (
    <Link href={`/products/${p.slug}`} className="plate product-card">
      <div className="model">
        <span>{p.sku}</span>
        <span>{p.warrantyMonths} MO</span>
      </div>
      <div className="art-well">
        <ProductArt sku={p.sku} name={p.name} />
      </div>
      <h3>{p.name}</h3>
      <p>{p.tagline}</p>
      <div className="price-line">
        <strong>{price(p.price)}</strong>
        <span className={stockClass(p)}>{stockLabel(p)}</span>
      </div>
    </Link>
  );
}
