// Reads the Markdown in ../content at build time. The same files feed the agent's knowledge
// base, so the website and the agent cannot disagree.
import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import { marked } from "marked";
import { slugify } from "./format";

// Formatting helpers live in ./format so client components can use them without node:fs.
export { price, slugify, STATUS_LABEL, stockLabel } from "./format";

const CONTENT = path.join(process.cwd(), "..", "content");
const EVAL_RESULTS = path.join(process.cwd(), "..", "evals", "results", "latest.json");

export type Product = {
  sku: string;
  name: string;
  slug: string;
  category: string;
  price: number;
  stock: number;
  restockDate?: string;
  warrantyMonths: number;
  colors: string[];
  tagline: string;
  html: string;
};

export type Page = { slug: string; title: string; html: string; meta: Record<string, unknown> };

export type Order = {
  order_id: string;
  customer_first_name: string;
  items: { sku: string; qty: number }[];
  status: string;
};

export type EvalReport = {
  timestamp: string;
  model: string | null;
  judge_model: string | null;
  summary: Record<string, unknown>;
};

export const CATEGORIES = ["Audio", "Smart Home", "Computing", "Mobile and Power"];

function render(markdown: string): string {
  // The page component renders its own title, so drop the Markdown H1.
  const body = markdown.replace(/^# .*\n+/m, "");
  return marked.parse(body, { async: false }) as string;
}

function read(file: string) {
  const { data, content } = matter(fs.readFileSync(file, "utf8"));
  return { data, content };
}

function isoDate(value: unknown): string | undefined {
  if (!value) return undefined;
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value);
}

export function getProducts(): Product[] {
  const dir = path.join(CONTENT, "products");
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md"))
    .sort()
    .map((f) => {
      const { data, content } = read(path.join(dir, f));
      return {
        sku: data.sku,
        name: data.name,
        slug: f.replace(/\.md$/, ""),
        category: data.category,
        price: Number(data.price),
        stock: Number(data.stock),
        restockDate: isoDate(data.restock_date),
        warrantyMonths: Number(data.warranty_months),
        colors: data.colors ?? [],
        tagline: data.tagline ?? "",
        html: render(content),
      };
    });
}

export function getProduct(slug: string): Product | undefined {
  return getProducts().find((p) => p.slug === slug);
}

export function getPage(slug: string): Page {
  const { data, content } = read(path.join(CONTENT, `${slug}.md`));
  return { slug, title: String(data.title ?? slug), html: render(content), meta: data };
}

export function getDocs(): Page[] {
  const dir = path.join(CONTENT, "docs");
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".md"))
    .map((f) => {
      const { data, content } = read(path.join(dir, f));
      return { slug: String(data.slug ?? f.replace(/\.md$/, "")), title: String(data.title), html: render(content), meta: data };
    })
    .sort((a, b) => Number(a.meta.order ?? 99) - Number(b.meta.order ?? 99));
}

export type FaqSection = { title: string; id: string; items: { q: string; id: string; html: string }[] };

/** content/faq.md split into sections (##) of questions (###). */
export function getFaq(): FaqSection[] {
  const { content } = read(path.join(CONTENT, "faq.md"));
  const sections: FaqSection[] = [];
  for (const block of content.split(/^## /m).slice(1)) {
    const [titleLine, ...rest] = block.split("\n");
    const items = rest
      .join("\n")
      .split(/^### /m)
      .slice(1)
      .map((qa) => {
        const [q, ...answer] = qa.split("\n");
        return { q: q.trim(), id: slugify(q), html: marked.parse(answer.join("\n").trim(), { async: false }) as string };
      });
    sections.push({ title: titleLine.trim(), id: slugify(titleLine), items });
  }
  return sections;
}

/** A product without its page HTML, small enough to pass to client components. */
export function toCard(p: Product) {
  const { html: _html, colors: _colors, ...card } = p;
  void _html;
  void _colors;
  return card;
}

export function getOrders(): Order[] {
  return JSON.parse(fs.readFileSync(path.join(CONTENT, "orders.json"), "utf8"));
}

export function getLatestEval(): EvalReport | null {
  try {
    return JSON.parse(fs.readFileSync(EVAL_RESULTS, "utf8"));
  } catch {
    return null;
  }
}
