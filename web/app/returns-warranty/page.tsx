import type { Metadata } from "next";
import ProsePage from "@/components/ProsePage";

export const metadata: Metadata = { title: "Returns and warranty" };

export default function Page() {
  return <ProsePage slug="returns-warranty" lede="30 days to return, and up to 24 months of warranty." />;
}
