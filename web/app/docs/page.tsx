import type { Metadata } from "next";
import Link from "next/link";
import { getDocs, getOrders, getProducts, STATUS_LABEL } from "@/lib/content";

export const metadata: Metadata = { title: "Docs" };

export default function DocsPage() {
  const docs = getDocs();
  const names = Object.fromEntries(getProducts().map((p) => [p.sku, p.name]));
  const orders = getOrders();

  return (
    <div className="wrap page">
      <header className="page-head">
        <h1>Docs</h1>
        <p className="lede">Setup guides for our products, and the sample orders you can ask Volt about.</p>
      </header>

      <h2>Setup guides</h2>
      <ul className="doc-list">
        {docs.map((d) => (
          <li key={d.slug}>
            <Link href={`/docs/${d.slug}`}>
              {d.title}
              <span>{guideSummary(d.meta.products as string[] | undefined, names)}</span>
            </Link>
          </li>
        ))}
      </ul>

      <h2 id="demo-orders">Demo orders</h2>
      <p className="prose">
        This store is a demonstration, so these orders are samples. Ask Volt about any of them by order number. Delivery
        dates are worked out from the day the server starts.
      </p>
      <table className="table" style={{ maxWidth: 760 }}>
        <thead>
          <tr>
            <th scope="col">Order number</th>
            <th scope="col">Customer</th>
            <th scope="col">Items</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.order_id}>
              <td className="order-id">{o.order_id}</td>
              <td>{o.customer_first_name}</td>
              <td>{o.items.map((i) => `${i.qty > 1 ? `${i.qty} x ` : ""}${names[i.sku] ?? i.sku}`).join(", ")}</td>
              <td>{STATUS_LABEL[o.status] ?? o.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function guideSummary(skus: string[] | undefined, names: Record<string, string>) {
  if (!skus?.length) return "Order numbers, statuses and carriers";
  return skus.map((s) => `${names[s] ?? s} (${s})`).join(" and ");
}
