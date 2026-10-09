import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  reactCompiler: true,
  // Dashboard бүхэлдээ client-side SPA (нэвтрэлт шаарддаг) тул dev-ийн auto 'instant' validation шаардлагагүй
  experimental: { instantInsights: { validationLevel: "manual-warning" } },
  // Vercel: browser зөвхөн өөрийн домэйн рүү хандана, login cookie first-party болно
  async rewrites() {
    const backend = process.env.BACKEND_ORIGIN?.replace(/\/$/, "");
    return backend ? [{ source: "/api/:path*", destination: `${backend}/api/:path*` }] : [];
  },
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
