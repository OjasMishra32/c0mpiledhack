#!/usr/bin/env node
// Scans src/ for visual patterns daviduichanges.txt explicitly forbids.
// Run: node scripts/visual-lint.mjs

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const SRC = new URL('../src', import.meta.url).pathname;

const RULES = [
  { name: 'linear-gradient', pattern: /linear-gradient/g },
  { name: 'radial-gradient', pattern: /radial-gradient/g },
  { name: 'colored drop-shadow', pattern: /drop-shadow\([^)]*(#|rgb)/gi },
  { name: 'glow-style box-shadow', pattern: /box-shadow:\s*[^;]*\b(0\s+0\s+\d+px)/gi },
  {
    name: 'rounded-full outside approved controls',
    pattern: /rounded-full/g,
    // small status indicators / worker identity markers are an explicitly approved exception (section 5)
    exceptions: ['StatusIndicator.tsx', 'WorkerRow.tsx'],
  },
  { name: 'animate-pulse', pattern: /animate-pulse/g },
  { name: 'animate-ping', pattern: /animate-ping/g },
  { name: 'infinite animation outside loading', pattern: /animation:[^;]*\binfinite\b/gi, exceptions: ['shimmer'] },
];

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const s = statSync(full);
    if (s.isDirectory()) walk(full, files);
    else if (['.ts', '.tsx', '.css'].includes(extname(full))) files.push(full);
  }
  return files;
}

let violations = 0;
for (const file of walk(SRC)) {
  const content = readFileSync(file, 'utf8');
  const basename = file.split('/').pop();
  for (const rule of RULES) {
    if (rule.exceptions?.some((ex) => content.includes(ex) || basename === ex)) continue;
    const matches = content.match(rule.pattern);
    if (matches) {
      violations += matches.length;
      console.error(`${file}: ${matches.length}x "${rule.name}"`);
    }
  }
}

if (violations > 0) {
  console.error(`\n${violations} visual regression(s) found.`);
  process.exit(1);
} else {
  console.log('No forbidden visual patterns found.');
}
