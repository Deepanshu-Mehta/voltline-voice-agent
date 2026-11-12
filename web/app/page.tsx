import Link from "next/link";
import ProductArt from "@/components/ProductArt";
import ProductCard from "@/components/ProductCard";
import { CATEGORIES, getOrders, getProducts, slugify, toCard } from "@/lib/content";

const HINTS = [
  "Do SafeView cameras work on 5 GHz Wi-Fi?",
  "Can I take the PowerCore on a plane?",
  "Where is order 4378?",
];

// The product drawn on each range tile
const RANGE_ART: Record<string, string> = {
  Audio: "VL-H500",
  "Smart Home": "VL-C420",
  Computing: "VL-K800",
  "Mobile and Power": "VL-B200",
};

const FEATURED = ["VL-A300", "VL-T700", "VL-D900", "VL-G650"];

export default function Home() {
  const products = getProducts();
  const orders = getOrders().slice(0, 4);
  const featured = FEATURED.map((sku) => products.find((p) => p.sku === sku)!).filter(Boolean);

  return (
    <>
      <section className="wrap hero">
        <div className="hero-copy">
          <h1>Ask about anything we sell.</h1>
          <p className="lede">
            Volt, our voice agent, knows every spec, price and policy on this site. It can also check your order, look
            up stock and open a support ticket.
          </p>
          <form className="ask" action="/talk" method="get" role="search">
            <label htmlFor="q" className="skip">
              Your question
            </label>
            <input id="q" name="q" type="text" placeholder="Which earbuds have noise cancelling?" required />
            <button className="button button-signal" type="submit">
              Ask Volt
            </button>
          </form>
          <ul className="ask-hints" aria-label="Example questions">
            {HINTS.map((h) => (
              <li key={h}>
                <Link className="chip" href={`/talk?q=${encodeURIComponent(h)}`}>
                  {h}
                </Link>
              </li>
            ))}
          </ul>
        </div>

        <figure className="plate transcript" aria-label="Example conversation with Volt">
          <div className="model">
            <span>VOLT</span>
            <span>EXAMPLE</span>
          </div>
          <div className="msg msg-user"><p>Where is order 4512?</p></div>
          <div className="msg msg-agent">
            <div className="who">Volt</div>
            <div className="tool-note">Looked up order 4512</div>
            <p>Order 4 5 1 2, your Pulse Buds Pro, has shipped with UPS and is due in two days.</p>
          </div>
          <div className="msg msg-user"><p>Can I return them if I open them?</p></div>
          <div className="msg msg-agent">
            <div className="who">Volt</div>
            <p>Opened earbuds can only be returned if they are defective. Unopened, you have 30 days.</p>
          </div>
        </figure>
      </section>

      <section className="wrap" aria-label="Store policies">
        <dl className="plate promise">
          <div><dt>Delivery</dt><dd>Free on orders of $50 or more</dd></div>
          <div><dt>Returns</dt><dd>30 days from delivery</dd></div>
          <div><dt>Warranty</dt><dd>12 months, 24 on Horizon and ThermoSense</dd></div>
          <div><dt>Support</dt><dd>Volt answers at any hour</dd></div>
        </dl>
      </section>

      <section className="wrap category" aria-labelledby="ranges">
        <h2 id="ranges">Shop by range</h2>
        <ul className="ranges">
          {CATEGORIES.map((c) => {
            const count = products.filter((p) => p.category === c).length;
            return (
              <li key={c}>
                <Link href={`/products#${slugify(c)}`} className="range-tile">
                  <div className="art-well">
                    <ProductArt sku={RANGE_ART[c]} name="" />
                  </div>
                  <span className="range-name">{c}</span>
                  <span className="range-count">{count} products</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="wrap category" aria-labelledby="featured">
        <div className="section-head">
          <h2 id="featured">Popular right now</h2>
          <Link href="/products">See all {products.length} products</Link>
        </div>
        <ul className="grid">
          {featured.map((p) => (
            <li key={p.sku}>
              <ProductCard product={toCard(p)} />
            </li>
          ))}
        </ul>
      </section>

      <section className="wrap category split" aria-labelledby="demo">
        <div>
          <h2 id="demo">Try it with a demo order</h2>
          <p>
            This is a demonstration store with 20 sample orders. Give Volt one of these order numbers and ask where it
            is. The full list is on the <Link href="/docs#demo-orders">Docs page</Link>.
          </p>
        </div>
        <ul className="doc-list">
          {orders.map((o) => (
            <li key={o.order_id}>
              <Link href={`/talk?q=${encodeURIComponent(`Where is order ${o.order_id}?`)}`}>
                <span className="order-id">{o.order_id}</span>
                <span>Ask where order {o.order_id} is</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
