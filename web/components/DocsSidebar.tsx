import Link from "next/link";
import { getDocs } from "@/lib/content";

export default function DocsSidebar({ current }: { current?: string }) {
  return (
    <nav className="sidebar" aria-label="Setup guides">
      <ul>
        <li>
          <Link href="/docs" aria-current={current ? undefined : "page"}>
            All guides
          </Link>
        </li>
        {getDocs().map((d) => (
          <li key={d.slug}>
            <Link href={`/docs/${d.slug}`} aria-current={d.slug === current ? "page" : undefined}>
              {d.title}
            </Link>
          </li>
        ))}
        <li>
          <Link href="/docs#demo-orders">Demo orders</Link>
        </li>
      </ul>
    </nav>
  );
}
