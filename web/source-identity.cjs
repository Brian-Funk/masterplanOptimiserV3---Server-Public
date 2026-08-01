/* eslint-disable @typescript-eslint/no-require-imports */
'use strict';

const { execFileSync } = require('node:child_process');

const COMMIT_PATTERN = /^[0-9a-f]{40}$/;

function git(repositoryRoot, ...args) {
  return execFileSync('git', ['-C', repositoryRoot, ...args], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim();
}

function normaliseRepositoryUrl(raw) {
  let value = String(raw || '').trim();
  const scp = value.match(/^git@([^:]+):(.+)$/);
  if (scp) {
    value = `https://${scp[1]}/${scp[2]}`;
  } else {
    value = value.replace(/^ssh:\/\/git@([^/]+)\//, 'https://$1/');
  }
  value = value.replace(/\.git\/?$/, '').replace(/\/$/, '');
  const parsed = new URL(value);
  if (
    parsed.protocol !== 'https:' ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error('The public source repository must be a credential-free HTTPS URL.');
  }
  return parsed.toString().replace(/\/$/, '');
}

function validateSourceUrl(raw, revision) {
  const parsed = new URL(String(raw || '').trim());
  if (
    parsed.protocol !== 'https:' ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    !parsed.pathname.includes(revision)
  ) {
    throw new Error('The corresponding-source URL must be credential-free HTTPS and contain the exact revision.');
  }
  return parsed.toString();
}

function resolveSourceIdentity({ env = process.env, repositoryRoot } = {}) {
  if (!repositoryRoot) {
    throw new Error('repositoryRoot is required');
  }
  const repositoryUrl = normaliseRepositoryUrl(
    env.MP_PUBLIC_SOURCE_REPOSITORY_URL || git(repositoryRoot, 'remote', 'get-url', 'origin'),
  );
  const revision = String(
    env.MP_PUBLIC_SOURCE_REVISION || git(repositoryRoot, 'rev-parse', 'HEAD'),
  ).trim().toLowerCase();
  if (!COMMIT_PATTERN.test(revision)) {
    throw new Error('The public source revision must be an exact 40-character Git commit SHA.');
  }
  const defaultSourceUrl = repositoryUrl.includes('github.com/')
    ? `${repositoryUrl}/tree/${revision}`
    : '';
  const sourceUrl = validateSourceUrl(
    env.MP_PUBLIC_SOURCE_URL || defaultSourceUrl,
    revision,
  );
  return { repositoryUrl, revision, sourceUrl };
}

module.exports = {
  normaliseRepositoryUrl,
  resolveSourceIdentity,
  validateSourceUrl,
};
