import type { NextConfig } from "next";
import pkg from "./package.json";

// Static export: the FastAPI backend serves `out/` from the same origin in production
// (one Railway service). In dev, set NEXT_PUBLIC_API_URL=http://localhost:8000.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  // Shown in the header, so an old build in out/ is easy to spot.
  env: { NEXT_PUBLIC_UI_VERSION: pkg.version },
};

export default nextConfig;
