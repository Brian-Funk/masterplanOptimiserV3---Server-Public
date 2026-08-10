/* eslint-disable @typescript-eslint/no-require-imports */
const fs = require('node:fs');
const path = require('node:path');

const packageRoot = path.dirname(require.resolve('@fontsource/source-sans-3/400.css'));
const destination = path.join(__dirname, '..', 'public', 'fonts');
const files = [
  'source-sans-3-latin-400-normal.woff2',
  'source-sans-3-latin-400-italic.woff2',
  'source-sans-3-latin-600-normal.woff2',
  'source-sans-3-latin-700-normal.woff2',
];

fs.mkdirSync(destination, { recursive: true });
for (const file of files) {
  fs.copyFileSync(path.join(packageRoot, 'files', file), path.join(destination, file));
}

console.log(`Prepared ${files.length} self-hosted Source Sans 3 font files.`);
