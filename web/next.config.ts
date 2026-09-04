import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  experimental: {
    // Le proxy de réécriture doit accepter la limite backend (500 Mo par lot)
    // plus le léger surcoût multipart, sans quoi Next tronque le flux à 10 Mo.
    proxyClientMaxBodySize: 550 * 1024 * 1024,
  },
  async rewrites() {
    const api = process.env.INTERNAL_API_BASE ?? "http://127.0.0.1:8001";
    return [
      {
        source: "/backend/:path*",
        destination: `${api}/:path*`,
      },
    ];
  },
};

export default nextConfig;
