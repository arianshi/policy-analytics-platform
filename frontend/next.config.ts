import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // small self-contained server for the Docker image
  reactStrictMode: true,
  turbopack: { root: path.resolve(process.cwd()) },
};

export default nextConfig;
