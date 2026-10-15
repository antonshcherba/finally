const dev = process.env.NODE_ENV === 'development';

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  images: { unoptimized: true },
  ...(dev && {
    async rewrites() {
      return [{ source: '/api/:path*', destination: 'http://localhost:8000/api/:path*' }];
    },
  }),
};

export default nextConfig;
