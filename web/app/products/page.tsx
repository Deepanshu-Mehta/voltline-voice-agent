import type { Metadata } from "next";
import ProductBrowser from "@/components/ProductBrowser";
import { CATEGORIES, getProducts, toCard } from "@/lib/content";

export const metadata: Metadata = { title: "Products" };

export default function ProductsPage() {
  const products = getProducts();
  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>Products</h1>
        <p className="lede">
          {products.length} products in four ranges. Every product page lists its full specifications, what is in the
          box and its warranty.
        </p>
      </header>
      <ProductBrowser products={products.map(toCard)} categories={CATEGORIES} />
    </div>
  );
}
