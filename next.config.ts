import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // Required for BlockNote server components
    serverComponentsExternalPackages: ["pg"],
  },
};

export default nextConfig;
