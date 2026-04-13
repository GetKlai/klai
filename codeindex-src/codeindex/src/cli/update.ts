/**
 * Update Command
 *
 * Re-indexes the current repository. Must be run from inside a git repo
 * that was previously indexed with `codeindex analyze`.
 *
 * Usage: codeindex update [-f|--force] [--embeddings]
 */

import { getMainRepoRoot, getGitRoot, deriveProjectName } from '../storage/git.js';
import { findRegistryEntry } from '../storage/repo-manager.js';
import { analyzeCommand, AnalyzeOptions } from './analyze.js';
import { promptProjectName } from './prompt-utils.js';

export const updateCommand = async (options?: AnalyzeOptions) => {
  const mainRoot = getMainRepoRoot(process.cwd());
  const gitRoot = getGitRoot(process.cwd());
  const repoPath = mainRoot || gitRoot;

  if (!repoPath) {
    console.log('\n  Not inside a git repository\n');
    process.exitCode = 1;
    return;
  }

  const entry = await findRegistryEntry(process.cwd());
  if (!entry) {
    const defaultName = deriveProjectName(process.cwd());
    const projectName = await promptProjectName(defaultName);
    if (!projectName) {
      process.exitCode = 1;
      return;
    }
    await analyzeCommand(projectName, undefined, options);
    return;
  }

  // Delegate to analyzeCommand with the known project name
  await analyzeCommand(entry.name, undefined, options);
};
