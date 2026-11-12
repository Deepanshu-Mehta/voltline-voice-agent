import Link from "next/link";

export default function NotFound() {
  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>This page does not exist.</h1>
        <p className="lede">The link may be old, or the address mistyped. These pages are a good place to start.</p>
      </header>
      <ul className="doc-list">
        <li><Link href="/products">Products<span>All 14 products, by range</span></Link></li>
        <li><Link href="/faq">FAQ<span>Delivery, returns, warranty and products</span></Link></li>
        <li><Link href="/talk">Talk to Volt<span>Ask the agent anything about the store</span></Link></li>
      </ul>
    </div>
  );
}
