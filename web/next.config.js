/** @type {import('next').NextConfig} */
const path = require('node:path');
const { resolveSourceIdentity } = require('./source-identity.cjs');

const sourceIdentity = resolveSourceIdentity({
  repositoryRoot: path.resolve(__dirname, '..'),
});

const nextConfig = {
  output: 'export',
  turbopack: {
    root: __dirname,
  },
  env: {
    NEXT_PUBLIC_APP_VERSION: require('./package.json').version,
    NEXT_PUBLIC_SOURCE_REPOSITORY_URL: sourceIdentity.repositoryUrl,
    NEXT_PUBLIC_SOURCE_REVISION: sourceIdentity.revision,
    NEXT_PUBLIC_SOURCE_URL: sourceIdentity.sourceUrl,
  },
}

module.exports = nextConfig
