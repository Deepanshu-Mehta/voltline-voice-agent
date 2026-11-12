// Line drawings of each product, in the rating-plate style: graphite strokes on the plate,
// signal yellow only for status lights. Drawn on a 240 x 160 grid.
import type { ReactNode } from "react";

const SIGNAL = "var(--signal)";
const PAPER = "var(--paper)";
const INK = "currentColor";

function Light({ cx, cy, r = 3.5 }: { cx: number; cy: number; r?: number }) {
  return <circle cx={cx} cy={cy} r={r} fill={SIGNAL} stroke="none" />;
}

function Bud({ x, y, flip = false, short = false }: { x: number; y: number; flip?: boolean; short?: boolean }) {
  const s = flip ? -1 : 1;
  return (
    <g transform={`translate(${x} ${y}) scale(${s} 1)`}>
      <rect x={-5} y={10} width={11} height={short ? 26 : 38} rx={5.5} transform="rotate(-14)" fill={PAPER} />
      <circle cx={0} cy={0} r={15} fill={PAPER} />
      <ellipse cx={-11} cy={-3} rx={5} ry={7} fill={INK} stroke="none" />
    </g>
  );
}

function grid(cols: number, rows: number, x0: number, y0: number, w: number, h: number, gap: number) {
  const keys: ReactNode[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      keys.push(<rect key={`${r}-${c}`} x={x0 + c * (w + gap)} y={y0 + r * (h + gap)} width={w} height={h} rx={2} strokeWidth={1.6} />);
    }
  }
  return keys;
}

const ART: Record<string, ReactNode> = {
  // Pulse Buds: two buds over a pill-shaped case
  "VL-A100": (
    <>
      <rect x={72} y={86} width={96} height={52} rx={26} fill={PAPER} />
      <path d="M74 106 H166" />
      <Light cx={120} cy={124} />
      <Bud x={70} y={50} />
      <Bud x={170} y={50} flip />
    </>
  ),
  // Pulse Buds Pro: short-stem buds, dark case, wireless charging arcs
  "VL-A300": (
    <>
      <path d="M86 150 q34 -10 68 0" strokeWidth={2} />
      <path d="M98 156 q22 -6 44 0" strokeWidth={2} />
      <rect x={76} y={84} width={88} height={54} rx={22} fill={INK} />
      <path d="M78 104 H162" stroke={PAPER} />
      <Light cx={120} cy={122} />
      <Bud x={72} y={52} short />
      <Bud x={168} y={52} short flip />
    </>
  ),
  // Horizon: over-ear headphones
  "VL-H500": (
    <>
      <path d="M64 102 V78 a56 56 0 0 1 112 0 V102" strokeWidth={6} />
      <path d="M64 86 a56 56 0 0 1 112 0" strokeWidth={2} transform="translate(0 -10)" />
      <rect x={46} y={88} width={34} height={58} rx={14} fill={INK} />
      <rect x={160} y={88} width={34} height={58} rx={14} fill={INK} />
      <rect x={74} y={94} width={10} height={46} rx={5} fill={PAPER} />
      <rect x={156} y={94} width={10} height={46} rx={5} fill={PAPER} />
      <Light cx={63} cy={136} r={3} />
    </>
  ),
  // Boom Mini: capsule speaker with grille and strap
  "VL-S200": (
    <>
      <path d="M186 72 q26 14 0 30" strokeWidth={4} />
      <rect x={40} y={50} width={150} height={74} rx={37} fill={PAPER} />
      {Array.from({ length: 5 }, (_, r) =>
        Array.from({ length: 11 }, (_, c) => (
          <circle key={`${r}-${c}`} cx={70 + c * 9} cy={67 + r * 10} r={1.8} fill={INK} stroke="none" />
        )),
      )}
      <rect x={104} y={44} width={22} height={8} rx={4} fill={INK} />
    </>
  ),
  // SafeView Indoor: pan-tilt sphere on a base
  "VL-C420": (
    <>
      <path d="M84 146 h72 l-10 -18 h-52 z" fill={PAPER} />
      <path d="M112 128 v-12 h16 v12" />
      <circle cx={120} cy={72} r={44} fill={PAPER} />
      <circle cx={120} cy={68} r={20} fill={INK} />
      <circle cx={120} cy={68} r={8} fill={PAPER} stroke="none" />
      <circle cx={124} cy={64} r={2.5} fill={INK} stroke="none" />
      <Light cx={120} cy={102} r={3} />
    </>
  ),
  // SafeView Outdoor: weatherproof body with hood on a wall mount
  "VL-C450": (
    <>
      <rect x={180} y={50} width={22} height={64} rx={4} fill={PAPER} />
      <path d="M180 82 h-30 l-12 -8" strokeWidth={5} />
      <rect x={40} y={52} width={104} height={56} rx={14} fill={PAPER} />
      <path d="M36 52 h112 v-8 a6 6 0 0 0 -6 -6 h-100 a6 6 0 0 0 -6 6 z" fill={INK} />
      <circle cx={68} cy={80} r={16} fill={INK} />
      <circle cx={68} cy={80} r={6} fill={PAPER} stroke="none" />
      <rect x={96} y={70} width={34} height={20} rx={4} />
      <Light cx={124} cy={98} r={3} />
    </>
  ),
  // Smart Plug 2-Pack: two plugs, one behind the other
  "VL-P110": (
    <>
      <rect x={112} y={34} width={84} height={84} rx={20} fill={PAPER} />
      <rect x={46} y={58} width={84} height={84} rx={20} fill={PAPER} />
      <rect x={72} y={86} width={7} height={20} rx={3} fill={INK} stroke="none" />
      <rect x={97} y={86} width={7} height={20} rx={3} fill={INK} stroke="none" />
      <path d="M82 124 a6 6 0 0 1 12 0 v4 h-12 z" fill={INK} stroke="none" />
      <Light cx={118} cy={72} r={3} />
    </>
  ),
  // ThermoSense: square unit with a round display
  "VL-T700": (
    <>
      <rect x={66} y={26} width={108} height={108} rx={22} fill={PAPER} />
      <circle cx={120} cy={80} r={38} fill={INK} />
      <text x={120} y={92} textAnchor="middle" fontSize={30} fontWeight={800} fill={PAPER} stroke="none"
        style={{ fontVariationSettings: '"wdth" 125' }}>70°</text>
      <path d="M94 62 a30 30 0 0 1 22 -12" stroke={SIGNAL} strokeWidth={4} />
      <Light cx={120} cy={126} r={3} />
    </>
  ),
  // Keystone: 75% keyboard
  "VL-K800": (
    <>
      <rect x={22} y={44} width={196} height={84} rx={10} fill={PAPER} />
      {grid(14, 4, 32, 54, 10, 10, 3)}
      <rect x={32} y={106} width={20} height={10} rx={2} strokeWidth={1.6} />
      <rect x={58} y={106} width={96} height={10} rx={2} strokeWidth={1.6} />
      <rect x={160} y={106} width={10} height={10} rx={2} strokeWidth={1.6} />
      <rect x={173} y={106} width={10} height={10} rx={2} strokeWidth={1.6} />
      <rect x={186} y={106} width={10} height={10} rx={2} strokeWidth={1.6} />
      <rect x={199} y={54} width={10} height={10} rx={2} fill={SIGNAL} strokeWidth={1.6} />
    </>
  ),
  // Glide: mouse, top view
  "VL-M250": (
    <>
      <path d="M120 22 c34 0 46 26 46 62 c0 34 -18 56 -46 56 c-28 0 -46 -22 -46 -56 c0 -36 12 -62 46 -62 z" fill={PAPER} />
      <path d="M120 22 v46" />
      <path d="M76 68 h88" strokeWidth={2} />
      <rect x={115} y={38} width={10} height={20} rx={5} fill={INK} />
      <Light cx={120} cy={120} r={3} />
    </>
  ),
  // Dockline: slim dock with ports and an attached cable
  "VL-D900": (
    <>
      <path d="M200 92 q26 0 22 -30 q-4 -24 -30 -26" strokeWidth={4} />
      <rect x={188} y={30} width={14} height={10} rx={3} fill={INK} />
      <rect x={24} y={72} width={176} height={40} rx={8} fill={PAPER} />
      <rect x={36} y={84} width={18} height={9} rx={2} fill={INK} stroke="none" />
      <rect x={60} y={84} width={18} height={9} rx={2} fill={INK} stroke="none" />
      <rect x={86} y={84} width={13} height={8} rx={1.5} />
      <rect x={104} y={84} width={13} height={8} rx={1.5} />
      <rect x={122} y={84} width={13} height={8} rx={1.5} />
      <rect x={142} y={84} width={14} height={14} rx={2} />
      <rect x={162} y={86} width={10} height={4} rx={2} fill={INK} stroke="none" />
      <circle cx={186} cy={89} r={3} />
      <Light cx={36} cy={104} r={2.5} />
    </>
  ),
  // ClearCam: webcam clipped to a monitor edge
  "VL-W270": (
    <>
      <path d="M40 132 h160" strokeWidth={6} />
      <path d="M104 108 v24 h32 v-24" fill={PAPER} />
      <rect x={52} y={56} width={136} height={52} rx={26} fill={PAPER} />
      <circle cx={120} cy={82} r={18} fill={INK} />
      <circle cx={120} cy={82} r={7} fill={PAPER} stroke="none" />
      <circle cx={80} cy={82} r={2} fill={INK} stroke="none" />
      <circle cx={160} cy={82} r={2} fill={INK} stroke="none" />
      <Light cx={146} cy={70} r={3} />
    </>
  ),
  // PowerCore: power bank with charge lights
  "VL-B200": (
    <>
      <rect x={78} y={20} width={84} height={124} rx={16} fill={PAPER} />
      <rect x={110} y={14} width={20} height={6} rx={3} fill={INK} />
      <Light cx={106} cy={124} r={3} />
      <Light cx={116} cy={124} r={3} />
      <Light cx={126} cy={124} r={3} />
      <circle cx={136} cy={124} r={3} strokeWidth={1.6} />
      <path d="M126 58 l-12 22 h12 l-10 22" strokeWidth={4} />
    </>
  ),
  // ChargeMax: cube charger with prongs and three ports
  "VL-G650": (
    <>
      <rect x={94} y={22} width={8} height={24} rx={2} fill={INK} />
      <rect x={138} y={22} width={8} height={24} rx={2} fill={INK} />
      <rect x={70} y={44} width={100} height={100} rx={18} fill={PAPER} />
      <rect x={104} y={70} width={32} height={9} rx={4.5} fill={INK} stroke="none" />
      <rect x={104} y={88} width={32} height={9} rx={4.5} fill={INK} stroke="none" />
      <rect x={106} y={106} width={28} height={10} rx={1.5} />
      <text x={120} y={136} textAnchor="middle" fontSize={11} fontWeight={800} fill={INK} stroke="none"
        style={{ fontVariationSettings: '"wdth" 125' }}>65W</text>
    </>
  ),
};

export default function ProductArt({ sku, name, className }: { sku: string; name: string; className?: string }) {
  return (
    <svg viewBox="0 0 240 160" role="img" aria-label={name} className={className ?? "product-art"}>
      <g fill="none" stroke={INK} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
        {ART[sku] ?? <rect x={60} y={30} width={120} height={100} rx={16} />}
      </g>
    </svg>
  );
}
