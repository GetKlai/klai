import { createInterface } from 'readline';

/**
 * Prompt the user for a project name when the repo hasn't been indexed yet.
 * Presents a friendly 3-option menu.
 * Returns the chosen name, or null if the user skips.
 */
export const promptProjectName = async (defaultName: string): Promise<string | null> => {
  console.log('');
  console.log('  CodeIndex — Index this project?\n');
  console.log(`  1) Yes, use "${defaultName}"`);
  console.log('  2) No, skip');
  console.log('  3) Yes, with a different name');
  console.log('');

  const rl = createInterface({ input: process.stdin, output: process.stdout });

  const ask = (question: string): Promise<string> =>
    new Promise((resolve) => {
      rl.question(question, (answer) => resolve(answer.trim()));
    });

  try {
    const choice = await ask('  Choice (1/2/3): ');

    if (choice === '2' || choice.toLowerCase() === 'no' || choice.toLowerCase() === 'skip' || choice.toLowerCase() === 'none') {
      console.log('  Skipped.\n');
      return null;
    }

    if (choice === '3') {
      const name = await ask('  Project name: ');
      if (!name) {
        console.log('  No name provided, skipped.\n');
        return null;
      }
      return name;
    }

    // Default: option 1 or any other input → use default name
    return defaultName;
  } finally {
    rl.close();
  }
};
