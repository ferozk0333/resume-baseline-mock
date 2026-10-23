import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",          // production image (frontend/Dockerfile): server.js with only the files it needs
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
