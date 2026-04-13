/**
 * Rename Command
 *
 * Renames a project in the CodeIndex registry and moves its storage directory.
 * Usage:
 *   codeindex rename NewName          (rename current repo's project)
 *   codeindex rename OldName NewName  (rename by explicit old name)
 */

import fs from 'fs/promises';
import path from 'path';
import { readRegistry, getStoragePath, getGlobalDir, findRegistryEntry } from '../storage/repo-manager.js';

export const renameCommand = async (firstArg: string, secondArg?: string) => {
  let oldName: string;
  let newName: string;

  if (secondArg) {
    // Two args: codeindex rename OldName NewName
    oldName = firstArg;
    newName = secondArg;
  } else {
    // One arg: codeindex rename NewName (rename current repo)
    newName = firstArg;
    const entry = await findRegistryEntry(process.cwd());
    if (!entry) {
      console.log('  Not inside an indexed project.\n');
      console.log('  Usage: codeindex rename <new-name>       (from inside a repo)');
      console.log('         codeindex rename <old> <new>       (by name)');
      process.exitCode = 1;
      return;
    }
    oldName = entry.name;
  }

  if (oldName === newName) {
    console.log(`  Already named "${newName}"\n`);
    return;
  }

  const entries = await readRegistry();
  const idx = entries.findIndex(e => e.name === oldName);

  if (idx < 0) {
    console.log(`  Project "${oldName}" not found in registry.\n`);
    console.log('  Available projects:');
    for (const e of entries) {
      console.log(`    - ${e.name} (${e.path})`);
    }
    process.exitCode = 1;
    return;
  }

  // Check if new name is already taken
  if (entries.some(e => e.name === newName)) {
    console.log(`  Project name "${newName}" is already in use.\n`);
    process.exitCode = 1;
    return;
  }

  const oldStoragePath = entries[idx].storagePath;
  const newStoragePath = getStoragePath(newName);

  // Move storage directory
  try {
    await fs.access(oldStoragePath);
    // Remove stale KuzuDB lock/wal files that could block the rename
    for (const suffix of ['.wal', '.lock']) {
      try { await fs.rm(path.join(oldStoragePath, `lbug.db${suffix}`), { force: true }); } catch {}
    }
    try {
      await fs.rename(oldStoragePath, newStoragePath);
    } catch (renameErr: any) {
      if (renameErr.code === 'ENOTEMPTY' || renameErr.code === 'EEXIST') {
        // Target exists (stale from previous rename/index) — remove it and retry
        await fs.rm(newStoragePath, { recursive: true, force: true });
        await fs.rename(oldStoragePath, newStoragePath);
      } else {
        throw renameErr;
      }
    }
  } catch (err: any) {
    if (err.code === 'ENOENT') {
      // Old storage doesn't exist, just create new path
      await fs.mkdir(newStoragePath, { recursive: true });
    } else {
      console.error(`  Failed to move storage: ${err.message}\n`);
      process.exitCode = 1;
      return;
    }
  }

  // Update registry entry
  entries[idx].name = newName;
  entries[idx].storagePath = newStoragePath;

  const registryPath = path.join(getGlobalDir(), 'registry.json');
  await fs.mkdir(getGlobalDir(), { recursive: true });
  await fs.writeFile(registryPath, JSON.stringify(entries, null, 2), 'utf-8');

  console.log(`\n  Renamed: ${oldName} → ${newName}`);
  console.log(`  Storage: ${newStoragePath}\n`);
};
