import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // standalone: small self-contained server for the Docker image. export: static files the backend serves
  // itself (the single-image OpenShift deploy, built with NEXT_OUTPUT=export). On Vercel, Vercel's own output.
  output: process.env.NEXT_OUTPUT === "export" ? "export" : process.env.VERCEL ? undefined : "standalone",
  reactStrictMode: true,
  turbopack: { root: path.resolve(process.cwd()) },
};

export default nextConfig;
