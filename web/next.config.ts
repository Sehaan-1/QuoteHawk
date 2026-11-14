import type { NextConfig } from "next";

const staticExport = process.env.QUOTEHAWK_STATIC_EXPORT === "true";
const apiOrigin = process.env.QUOTEHAWK_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: staticExport ? "export" : undefined,
  ...(staticExport
    ? {}
    : {
        async rewrites() {
          return [
            {
              source: "/api/rfq/:path*",
              destination: `${apiOrigin}/api/rfq/:path*`,
            },
          ];
        },
      }),
};

export default nextConfig;
