import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: { workerThreads: true, useTypeScriptCli: false, cpus: 2 },
};

export default nextConfig;
