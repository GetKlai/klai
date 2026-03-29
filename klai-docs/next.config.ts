import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  basePath: "/docs",
  // Required for BlockNote server components (moved from experimental in Next.js 15)
  serverExternalPackages: ["pg"],
};

export default nextConfig;
