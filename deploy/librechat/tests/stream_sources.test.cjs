const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../../..');
const streamPatch = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patches/stream.cjs'),
  'utf8',
);

const sandbox = {
  Buffer,
  require: (id) => {
    if (id === './common/enum.cjs') {
      return {
        ContentTypes: {
          TEXT: 'text',
          THINK: 'think',
          THINKING: 'thinking',
          REASONING: 'reasoning',
          REASONING_CONTENT: 'reasoning_content',
          TOOL_CALL: 'tool_call',
          AGENT_UPDATE: 'agent_update',
          IMAGE_URL: 'image_url',
        },
        GraphEvents: {
          ON_RUN_STEP: 'on_run_step',
          ON_MESSAGE_DELTA: 'on_message_delta',
          ON_AGENT_UPDATE: 'on_agent_update',
          ON_REASONING_DELTA: 'on_reasoning_delta',
          ON_RUN_STEP_DELTA: 'on_run_step_delta',
          ON_RUN_STEP_COMPLETED: 'on_run_step_completed',
        },
        Providers: {
          OPENAI: 'openai',
          AZURE: 'azure',
          OPENROUTER: 'openrouter',
        },
        StepTypes: {
          MESSAGE_CREATION: 'message_creation',
          TOOL_CALLS: 'tool_calls',
        },
        ToolCallTypes: {
          TOOL_CALL: 'tool_call',
        },
      };
    }
    if (id === './tools/handlers.cjs') {
      return {};
    }
    if (
      id === './messages/core.cjs' ||
      id === './messages/ids.cjs' ||
      id === '@langchain/core/messages'
    ) {
      return {};
    }
    throw new Error(`Unexpected require: ${id}`);
  },
  console,
  exports: {},
};

vm.runInNewContext(streamPatch, sandbox);

const { createContentAggregator, extractKlaiSourcesFromText, getChunkSources } = sandbox.exports;
const sources = [
  {
    label: '1',
    title: 'Verantwoordelijkheden per bouwblok.pdf',
    artifact_id: 'artifact-responsibilities',
  },
];

assert.deepEqual(getChunkSources({ additional_kwargs: { sources } }), sources);

const marker = Buffer.from(JSON.stringify(sources), 'utf8')
  .toString('base64')
  .replace(/\+/g, '-')
  .replace(/\//g, '_')
  .replace(/=+$/, '');
assert.equal(
  JSON.stringify(extractKlaiSourcesFromText(`Antwoord. <!-- klai_sources=${marker} -->`)),
  JSON.stringify({ text: 'Antwoord. ', sources }),
);

const aggregator = createContentAggregator();
aggregator.aggregateContent({
  event: 'on_run_step',
  data: { id: 'step-1', index: 0, stepDetails: { type: 'message_creation' } },
});
aggregator.aggregateContent({
  event: 'on_message_delta',
  data: {
    id: 'step-1',
    delta: { content: { type: 'text', text: `Antwoord. <!-- klai_sources=${marker} -->` } },
  },
});
aggregator.aggregateContent({
  event: 'on_message_delta',
  data: {
    id: 'step-1',
    delta: { content: { type: 'text', text: 'Vervolg zonder metadata.' } },
  },
});

assert.equal(aggregator.contentParts[0].text, 'Antwoord. Vervolg zonder metadata.');
assert.equal(JSON.stringify(aggregator.contentParts[0].sources), JSON.stringify(sources));

console.log('OK: LibreChat stream aggregator preserves Klai source metadata.');
