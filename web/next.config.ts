import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // The site reads ../content and ../evals at build time, so the repository is the root.
  turbopack: { root: path.join(__dirname, "..") },
};

export default nextConfig;
