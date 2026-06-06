import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '../..');
const frontendDir = path.join(repoRoot, 'klai-portal/frontend');
const require = createRequire(path.join(frontendDir, 'package.json'));
const { chromium } = require('playwright');
const execFileAsync = promisify(execFile);

const storageState = path.join(
  frontendDir,
  'e2e/prod-tenant/_config/storageState.voys.json',
);

const args = process.argv.slice(2);
const onlyStep = args.find((arg) => arg.startsWith('--only='))?.split('=')[1];
const holdArg = args.find((arg) => arg.startsWith('--hold-ms='));
const holdMs = Number(holdArg?.split('=')[1] || process.env.VOYS_E2E_HOLD_MS || 300_000);

const steps = [
  {
    id: '01-strict-hubspot',
    mode: 'strict',
    prompt: 'Wat zegt onze kennisbank over Voys Freedom koppelen aan HubSpot?',
  },
  {
    id: '02-strict-unknown',
    mode: 'strict',
    prompt: 'Wat is het officiële recept voor tiramisu volgens onze Voys kennisbank?',
  },
  {
    id: '03-open-esim',
    mode: 'open',
    prompt:
      'Leg kort uit wat een eSIM is en vermeld alleen Voys-bronnen als onze kennisbank daar iets over zegt.',
    mustContain: ['Modus: Open'],
    mustNotContain: ['Overige problemen'],
  },
].filter((step) => !onlyStep || step.id === onlyStep);

if (steps.length === 0) {
  throw new Error(`No step matched --only=${onlyStep}`);
}

async function activateChrome() {
  if (process.platform !== 'darwin') return;
  try {
    await execFileAsync('osascript', ['-e', 'tell application "Google Chrome" to activate']);
  } catch {
    // Best-effort only; Playwright still runs if macOS focus cannot be changed.
  }
}

async function chatFrame(page) {
  await page.waitForFunction(
    () =>
      Array.from(document.querySelectorAll('iframe')).some((frame) =>
        frame.src.includes('chat-voys.getklai.com'),
      ),
    null,
    { timeout: 60_000 },
  );

  const frame = page
    .frames()
    .find((candidate) => candidate.url().includes('chat-voys.getklai.com'));
  if (!frame) {
    throw new Error('chat-voys iframe not found');
  }
  return frame;
}

async function chatBodyText(page) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      const frame = await chatFrame(page);
      return await frame.locator('body').innerText({ timeout: 10_000 });
    } catch (error) {
      if (!String(error).includes('Frame was detached') && attempt === 9) {
        throw error;
      }
      await page.waitForTimeout(1_000);
    }
  }
  throw new Error('failed to read chat frame body');
}

async function waitForAnswer(page, sentPrompt) {
  const started = Date.now();
  let previous = '';
  let stableCount = 0;

  while (Date.now() - started < 180_000) {
    const text = await chatBodyText(page);
    const afterPrompt = text.includes(sentPrompt)
      ? text.slice(text.lastIndexOf(sentPrompt) + sentPrompt.length)
      : text;

    const hasAgentActivity =
      afterPrompt.includes('Agent activiteit') ||
      afterPrompt.includes('Agent activity');
    const hasSources = afterPrompt.includes('Bronnen') || afterPrompt.includes('Sources');
    const hasRefusal =
      afterPrompt.includes('Dat staat niet in de kennisbank') ||
      afterPrompt.includes('Ik kan hierop geen antwoord geven') ||
      afterPrompt.includes('niet in de kennisbank');
    const hasSubstantiveText = afterPrompt.trim().length > 120;

    if (afterPrompt === previous) {
      stableCount += 1;
    } else {
      stableCount = 0;
      previous = afterPrompt;
    }

    if ((hasAgentActivity || hasSources || hasRefusal || hasSubstantiveText) && stableCount >= 2) {
      return afterPrompt.trim();
    }

    await page.waitForTimeout(2_000);
  }

  return previous.trim();
}

async function waitForPromptEcho(page, sentPrompt) {
  const started = Date.now();
  while (Date.now() - started < 30_000) {
    const text = await chatBodyText(page);
    if (text.includes(sentPrompt)) return;
    await page.waitForTimeout(500);
  }
  throw new Error(`prompt was not echoed in chat after pressing Enter: ${sentPrompt}`);
}

async function setMode(page, mode) {
  const wantedMode = mode === 'open' ? 'Open' : 'Strict';
  await page.getByRole('radio', { name: wantedMode }).click({ timeout: 10_000 });
  await page.waitForTimeout(500);
  return wantedMode;
}

function validateAnswer(step, answer) {
  for (const expected of step.mustContain || []) {
    if (!answer.includes(expected)) {
      throw new Error(`${step.id}: expected answer to contain ${JSON.stringify(expected)}`);
    }
  }
  for (const unexpected of step.mustNotContain || []) {
    if (answer.includes(unexpected)) {
      throw new Error(`${step.id}: answer unexpectedly contained ${JSON.stringify(unexpected)}`);
    }
  }
}

async function sendPrompt(page, step) {
  const wantedMode = await setMode(page, step.mode);
  const frame = await chatFrame(page);
  const input = frame.locator('#prompt-textarea');
  await input.waitFor({ state: 'visible', timeout: 60_000 });
  await input.fill(step.prompt);
  await input.focus();
  await input.press('Enter');
  await waitForPromptEcho(page, step.prompt);
  await page.bringToFront();
  await activateChrome();

  console.log(
    JSON.stringify(
      {
        phase: 'sent',
        step: step.id,
        mode: wantedMode,
        prompt: step.prompt,
      },
      null,
      2,
    ),
  );

  const answer = await waitForAnswer(page, step.prompt);
  validateAnswer(step, answer);
  const currentFrame = await chatFrame(page);
  const screenshotPath = path.join(
    repoRoot,
    `.context/voys-e2e-${Date.now()}-${step.id}.png`,
  );
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await page.bringToFront();
  await activateChrome();

  const result = {
    phase: 'result',
    step: step.id,
    mode: wantedMode,
    frameUrl: currentFrame.url(),
    answerPreview: answer.slice(0, 6000),
    screenshotPath,
  };
  console.log(JSON.stringify(result, null, 2));
  return result;
}

const browser = await chromium.launch({
  headless: false,
  channel: 'chrome',
  args: ['--start-maximized', '--window-position=80,60'],
});

try {
  const context = await browser.newContext({ storageState, viewport: null });
  const page = await context.newPage();
  await page.goto('https://voys.getklai.com/app/chat', {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForTimeout(4_000);
  await page.bringToFront();
  await activateChrome();

  if (page.url().includes('/login')) {
    throw new Error(`opened login page despite Voys storage state: ${page.url()}`);
  }

  const frame = await chatFrame(page);
  await frame.locator('#prompt-textarea').waitFor({ state: 'visible', timeout: 60_000 });

  console.log(
    JSON.stringify(
      {
        phase: 'ready',
        pageUrl: page.url(),
        frameUrl: frame.url(),
        steps: steps.map((step) => step.id),
        topBar: (await page.locator('body').innerText()).slice(0, 300),
      },
      null,
      2,
    ),
  );

  const results = [];
  for (const step of steps) {
    results.push(await sendPrompt(page, step));
  }

  console.log(
    JSON.stringify(
      {
        phase: 'complete',
        results: results.map((result) => ({
          step: result.step,
          mode: result.mode,
          screenshotPath: result.screenshotPath,
        })),
        holdingForMs: holdMs,
      },
      null,
      2,
    ),
  );

  await page.waitForTimeout(holdMs);
} catch (error) {
  console.error(
    JSON.stringify(
      {
        phase: 'error',
        message: error?.message || String(error),
        holdingForMs: holdMs,
      },
      null,
      2,
    ),
  );
  await activateChrome();
  await new Promise((resolve) => setTimeout(resolve, holdMs));
  throw error;
} finally {
  await browser.close();
}
