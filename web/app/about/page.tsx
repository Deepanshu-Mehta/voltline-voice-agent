import type { Metadata } from "next";
import ProsePage from "@/components/ProsePage";

export const metadata: Metadata = { title: "About" };

export default function Page() {
  return <ProsePage slug="about" />;
}
