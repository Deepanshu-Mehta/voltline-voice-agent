import type { Metadata } from "next";
import ProsePage from "@/components/ProsePage";

export const metadata: Metadata = { title: "Pricing and delivery" };

export default function Page() {
  return <ProsePage slug="pricing" lede="Delivery costs, offers, Voltline Care+ and how to pay." />;
}
