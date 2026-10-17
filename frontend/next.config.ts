import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required: the infra track builds the production Docker image from the
  // standalone output (copies .next/standalone + .next/static only).
  output: "standalone",
};

export default nextConfig;
