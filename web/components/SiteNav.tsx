"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

export const NAV: [string, string][] = [
  ["/products", "Products"],
  ["/pricing", "Pricing"],
  ["/docs", "Docs"],
  ["/faq", "FAQ"],
  ["/about", "About"],
  ["/benchmarks", "Benchmarks"],
];

function isActive(pathname: string | null, href: string) {
  return !!pathname && (pathname === href || pathname.startsWith(`${href}/`));
}

/** Main navigation with the current section marked. `pathname` is null in the static fallback. */
export function NavLinks({ pathname }: { pathname: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <nav aria-label="Main" className="site-nav" data-open={open}>
      <button
        type="button"
        className="menu-toggle"
        aria-expanded={open}
        aria-controls="main-menu"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "Close" : "Menu"}
      </button>
      <ul className="nav" id="main-menu">
        {NAV.map(([href, label]) => (
          <li key={href}>
            <Link
              href={href}
              aria-current={isActive(pathname, href) ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              {label}
            </Link>
          </li>
        ))}
        <li>
          <Link href="/talk" className="nav-talk" aria-current={pathname === "/talk" ? "page" : undefined}
            onClick={() => setOpen(false)}>
            Talk to us
          </Link>
        </li>
      </ul>
    </nav>
  );
}

export default function SiteNav() {
  return <NavLinks pathname={usePathname()} />;
}
