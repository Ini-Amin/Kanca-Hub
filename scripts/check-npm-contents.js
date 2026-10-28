#!/usr/bin/env node
// Refuse to publish if a credential-bearing file would ship in the tarball.
//
// `files` in package.json is an allowlist, but a directory entry still drags in
// everything inside it — including files that .gitignore hides. This runs as
// `prepack`, so `npm publish` and `npm pack` both fail loudly instead of
// uploading a live API key or a proxy pool with passwords.

const { execSync } = require('child_process');
const path = require('path');

const FORBIDDEN = [
  /(^|\/)config\.json$/,
  /(^|\/)proxies\.txt$/,
  /(^|\/)token_map\.json$/,
  /(^|\/)results[0-9]*\.json$/,
  /(^|\/)\.env$/,
  /accounts?\.json$/i,
  /credentials/i,
  /\.pem$/,
  /\.key$/,
];

function packedFiles() {
  // --dry-run --json prints the manifest without writing a tarball.
  const out = execSync('npm pack --dry-run --json --ignore-scripts', {
    cwd: path.join(__dirname, '..'),
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  });
  return JSON.parse(out)[0].files.map((f) => f.path);
}

const files = packedFiles();
const leaks = files.filter((p) => FORBIDDEN.some((re) => re.test(p)));

if (leaks.length) {
  console.error('\n✗ Refusing to publish — credential-bearing files in the tarball:\n');
  for (const f of leaks) console.error(`    ${f}`);
  console.error('\nFix the "files" allowlist in package.json, then retry.\n');
  process.exit(1);
}

console.log(`✓ npm contents check passed (${files.length} files, no credentials)`);
