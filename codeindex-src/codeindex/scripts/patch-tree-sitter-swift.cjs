#!/usr/bin/env node
/**
 * Installs and builds tree-sitter-swift's native binding.
 *
 * tree-sitter-swift cannot be a normal npm dependency because its binding.gyp
 * has "actions" that require tree-sitter-cli, which makes the npm install script
 * fail — and npm then removes the package entirely (since it's optional).
 *
 * This script uses `npm pack` + tar extraction to avoid npm's install-script
 * lifecycle entirely, then patches binding.gyp and rebuilds via node-gyp.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const SWIFT_PKG = 'tree-sitter-swift';
const SWIFT_VERSION = '0.6.0';
const rootDir = path.join(__dirname, '..');
const swiftDir = path.join(rootDir, 'node_modules', SWIFT_PKG);
const bindingPath = path.join(swiftDir, 'binding.gyp');
const bindingNode = path.join(swiftDir, 'build', 'Release', 'tree_sitter_swift_binding.node');

// Already built — nothing to do
if (fs.existsSync(bindingNode)) {
  process.exit(0);
}

try {
  // Step 1: Download and extract the package tarball (bypasses install scripts)
  if (!fs.existsSync(bindingPath)) {
    console.log(`[${SWIFT_PKG}] Downloading ${SWIFT_PKG}@${SWIFT_VERSION}...`);
    const tgz = execFileSync('npm', ['pack', `${SWIFT_PKG}@${SWIFT_VERSION}`, '--pack-destination', rootDir], {
      cwd: rootDir,
      encoding: 'utf8',
      timeout: 60000,
    }).trim();
    const tgzPath = path.join(rootDir, tgz);

    // Extract into node_modules
    fs.mkdirSync(swiftDir, { recursive: true });
    execFileSync('tar', ['xzf', tgzPath, '-C', swiftDir, '--strip-components=1'], {
      timeout: 30000,
    });
    fs.unlinkSync(tgzPath);
  }

  if (!fs.existsSync(bindingPath)) {
    console.warn(`[${SWIFT_PKG}] Package not found after download — skipping`);
    process.exit(0);
  }

  // Step 2: Patch binding.gyp — remove "actions" array that needs tree-sitter-cli
  const content = fs.readFileSync(bindingPath, 'utf8');
  if (content.includes('"actions"')) {
    const cleaned = content
      .replace(/#[^\n]*/g, '')           // strip Python-style comments
      .replace(/,\s*([\]}])/g, '$1');    // strip trailing commas
    const gyp = JSON.parse(cleaned);

    if (gyp.targets && gyp.targets[0] && gyp.targets[0].actions) {
      delete gyp.targets[0].actions;
      fs.writeFileSync(bindingPath, JSON.stringify(gyp, null, 2) + '\n');
      console.log(`[${SWIFT_PKG}] Patched binding.gyp (removed actions)`);
    }
  }

  // Step 3: Install sub-dependencies (node-addon-api, node-gyp-build)
  console.log(`[${SWIFT_PKG}] Installing dependencies...`);
  execFileSync('npm', ['install', '--ignore-scripts', '--no-audit', '--no-fund'], {
    cwd: swiftDir,
    stdio: 'pipe',
    timeout: 60000,
  });

  // Step 4: Rebuild native binding
  console.log(`[${SWIFT_PKG}] Building native binding...`);
  execFileSync('npx', ['node-gyp', 'rebuild'], {
    cwd: swiftDir,
    stdio: 'pipe',
    timeout: 120000,
  });
  console.log(`[${SWIFT_PKG}] Native binding built successfully`);
} catch (err) {
  console.warn(`[${SWIFT_PKG}] Could not build native binding:`, err.message);
  console.warn(`[${SWIFT_PKG}] Swift files will be skipped during indexing.`);
  // Clean up partial install to allow retry
  try { fs.rmSync(swiftDir, { recursive: true, force: true }); } catch {}
}
