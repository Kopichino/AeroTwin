/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    // Websocket upgrades are not proxied by `rewrites`, so the browser needs the
    // API origin explicitly in development.
    NEXT_PUBLIC_WS_URL:
      process.env.NEXT_PUBLIC_WS_URL ??
      (process.env.NODE_ENV === 'development' ? 'ws://localhost:8000/ws/v1' : ''),
  },
  // The API and websocket run on a separate origin in development. Proxying
  // through Next keeps the browser on one origin, so no CORS preflight and no
  // separate websocket URL configuration.
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
    return [{ source: '/api/v1/:path*', destination: `${api}/api/v1/:path*` }];
  },
};
export default nextConfig;
