/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output keeps the production Docker image small — it bundles
  // only the files needed to run `node server.js`, not the full node_modules tree.
  output: 'standalone',
};

export default nextConfig;
