import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Archivo } from "next/font/google";
import Launcher from "@/components/Launcher";
import SiteNav, { NavLinks } from "@/components/SiteNav";
import "./globals.css";

// One family; the width axis does the work. Expanded for headlines and model numbers, the way
// a product's rating plate is printed, normal width for reading.
const archivo = Archivo({ subsets: ["latin"], axes: ["wdth"], variable: "--font-archivo" });

export const metadata: Metadata = {
  title: { default: "Voltline Electronics", template: "%s | Voltline" },
  description:
    "Audio, smart home, computing and charging products, with a voice agent that answers questions about all of them.",
};

const FOOTER: [string, [string, string][]][] = [
  ["Shop", [
    ["/products#audio", "Audio"],
    ["/products#smart-home", "Smart Home"],
    ["/products#computing", "Computing"],
    ["/products#mobile-and-power", "Mobile and Power"],
  ]],
  ["Help", [
    ["/talk", "Talk to Volt"],
    ["/faq", "FAQ"],
    ["/docs", "Setup guides"],
    ["/pricing", "Delivery and offers"],
    ["/returns-warranty", "Returns and warranty"],
  ]],
  ["Company", [
    ["/about", "About Voltline"],
    ["/benchmarks", "Agent benchmarks"],
  ]],
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={archivo.variable}>
      <body>
        <a className="skip" href="#main">Skip to content</a>
        <header className="site-header">
          <div className="wrap header-row">
            <Link href="/" className="wordmark" aria-label="Voltline Electronics home">
              VOLTLINE
            </Link>
            {/* The current-page marker needs the URL; the fallback is the same menu without it. */}
            <Suspense fallback={<NavLinks pathname={null} />}>
              <SiteNav />
            </Suspense>
          </div>
        </header>
        <main id="main">{children}</main>
        <footer className="site-footer">
          <div className="wrap">
            <div className="footer-grid">
              <div className="footer-brand">
                <span className="wordmark">VOLTLINE</span>
                <p>Everyday audio, smart home, computing and charging products, sold online across the United States.</p>
              </div>
              {FOOTER.map(([title, links]) => (
                <nav key={title} aria-label={title}>
                  <h2>{title}</h2>
                  <ul>
                    {links.map(([href, label]) => (
                      <li key={href}>
                        <Link href={href}>{label}</Link>
                      </li>
                    ))}
                  </ul>
                </nav>
              ))}
            </div>
            <p className="footer-note">
              Voltline Electronics is a fictional store, built to demonstrate a voice agent. Prices are in US dollars
              and exclude sales tax.
            </p>
          </div>
        </footer>
        <Suspense fallback={null}>
          <Launcher />
        </Suspense>
      </body>
    </html>
  );
}
