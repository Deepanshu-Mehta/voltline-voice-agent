// Pure formatting helpers, safe to import from client components (no node:fs).

export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

export function price(n: number): string {
  return `$${n.toFixed(2)}`;
}

export function stockLabel(p: { stock: number; restockDate?: string }): string {
  if (p.stock === 0) {
    const d = p.restockDate ? new Date(`${p.restockDate}T12:00:00`) : null;
    return d ? `Out of stock until ${d.toLocaleDateString("en-US", { month: "long", day: "numeric" })}` : "Out of stock";
  }
  return p.stock < 10 ? `Low stock: ${p.stock} left` : "In stock";
}

export const STATUS_LABEL: Record<string, string> = {
  processing: "Processing",
  shipped: "Shipped",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
  cancelled: "Cancelled",
  refunded: "Refunded",
};
