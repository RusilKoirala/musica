import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";
// Where the Python API runs while developing. In production FastAPI serves this build itself.
const backend = process.env.MUSICA_BACKEND ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // A plain static build, so the Python server can serve it without Node at runtime.
  output: isDev ? undefined : "export",
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
    },
  }),
};

export default nextConfig;
