import type { NextConfig } from "next";

// Static export: the FastAPI backend serves `out/` from the same origin in production
// (one Railway service). In dev, set NEXT_PUBLIC_API_URL=http://localhost:8000.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
