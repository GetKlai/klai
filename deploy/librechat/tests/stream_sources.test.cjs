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
          ERROR: 'error',
          SUMMARY: 'summary',
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
        LOCAL_CODING_BUNDLE_NAMES: [],
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
    if (id === './utils/truncation.cjs') {
      return {};
    }
    if (id === './utils/events.cjs') {
      return { emitAgentLog() {} };
    }
    if (id === 'uuid') {
      return {};
    }
    if (id === './tools/eagerEventExecution.cjs') {
      return {};
    }
    if (id === './tools/streamedToolCallSeals.cjs') {
      return {};
    }
    if (id === './tools/toolOutputReferences.cjs') {
      return { TOOL_OUTPUT_REF_PATTERN: /\{\{tool_output:[^}]+\}\}/ };
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

const repeatedMessageCreationAggregator = createContentAggregator();
repeatedMessageCreationAggregator.aggregateContent({
  event: 'on_run_step',
  data: { id: 'step-repeat', index: 0, stepDetails: { type: 'message_creation' } },
});
repeatedMessageCreationAggregator.aggregateContent({
  event: 'on_message_delta',
  data: {
    id: 'step-repeat',
    delta: { content: { type: 'text', text: 'Klai typt zichtbaar. ' } },
  },
});
repeatedMessageCreationAggregator.aggregateContent({
  event: 'on_run_step',
  data: { id: 'step-repeat', index: 0, stepDetails: { type: 'message_creation' } },
});
repeatedMessageCreationAggregator.aggregateContent({
  event: 'on_message_delta',
  data: {
    id: 'step-repeat',
    delta: { content: { type: 'text', text: 'Bij compleet antwoord blijft dit staan.' } },
  },
});

assert.equal(
  repeatedMessageCreationAggregator.contentParts[0].text,
  'Klai typt zichtbaar. Bij compleet antwoord blijft dit staan.',
);

const multiPartFinalDeltaAggregator = createContentAggregator();
multiPartFinalDeltaAggregator.aggregateContent({
  event: 'on_run_step',
  data: { id: 'step-multipart', index: 0, stepDetails: { type: 'message_creation' } },
});
multiPartFinalDeltaAggregator.aggregateContent({
  event: 'on_message_delta',
  data: {
    id: 'step-multipart',
    delta: {
      content: [
        { type: 'text', text: '' },
        { type: 'text', text: 'Final text from the same completed delta.' },
      ],
    },
  },
});

assert.equal(
  multiPartFinalDeltaAggregator.contentParts[0].text,
  'Final text from the same completed delta.',
);

const toolAggregator = createContentAggregator();
toolAggregator.aggregateContent({
  event: 'on_run_step',
  data: { id: 'msg-empty', index: 0, stepDetails: { type: 'message_creation' } },
});
toolAggregator.aggregateContent({
  event: 'on_run_step',
  data: {
    id: 'tool-step',
    index: 1,
    stepDetails: {
      type: 'tool_calls',
      tool_calls: [{ id: 'call_1', name: 'search_knowledge', args: '{}' }],
    },
  },
});
toolAggregator.aggregateContent({
  event: 'on_run_step',
  data: { id: 'msg-final', index: 2, stepDetails: { type: 'message_creation' } },
});
toolAggregator.aggregateContent({
  event: 'on_message_delta',
  data: {
    id: 'msg-final',
    delta: { content: { type: 'text', text: 'Final answer after tool.' } },
  },
});

assert.equal(toolAggregator.contentParts.length, 3);
assert.equal(toolAggregator.contentParts[0], undefined);
assert.equal(toolAggregator.contentParts[1].type, 'tool_call');
assert.equal(toolAggregator.contentParts[2].text, 'Final answer after tool.');

console.log('OK: LibreChat stream aggregator preserves Klai source metadata.');
