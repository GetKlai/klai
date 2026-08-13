const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../../..');
const canaryPatchDir = path.join(repoRoot, 'deploy/librechat/getklai/patches');

class BaseMessage {
  constructor(fields = {}) {
    Object.assign(this, fields);
  }
}

class HumanMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'user';
  }
}

class AIMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'assistant';
  }
}

class AIMessageChunk extends AIMessage {}

class SystemMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'system';
  }
}

class ToolMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'tool';
  }
}

const commonEnum = {
  Constants: {
    LC_TRANSFER_TO_: 'transfer_to_',
    TOOL_SEARCH: 'tool_search',
  },
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
    ANTHROPIC: 'anthropic',
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

function loadPatch(fileName, stubs = {}) {
  const patch = fs.readFileSync(path.join(canaryPatchDir, fileName), 'utf8');
  const sandbox = {
    Buffer,
    console,
    exports: {},
    require: (id) => {
      if (id === '@langchain/core/messages') {
        return { HumanMessage, AIMessage, AIMessageChunk, SystemMessage, ToolMessage };
      }
      if (id === '../common/enum.cjs' || id === './common/enum.cjs') {
        return commonEnum;
      }
      if (id === '../common/index.cjs' || id === './common/index.cjs') {
        return {};
      }
      if (id === '../utils/events.cjs' || id === './utils/events.cjs') {
        return { emitAgentLog() {} };
      }
      if (id === './langchain.cjs') {
        return {
          toLangChainContent: (parts) => parts,
          toLangChainMessageFields: (message) => message,
        };
      }
      if (id === '../llm/anthropic/utils/message_inputs.cjs') {
        return { normalizeAnthropicToolCallId: (toolCallId) => toolCallId };
      }
      if (id === './tools/handlers.cjs') {
        return {};
      }
      if (id === './messages/core.cjs' || id === './messages/ids.cjs' || id === './messages/index.cjs') {
        return {};
      }
      if (id === './utils/truncation.cjs') {
        return {};
      }
      if (id === './utils/llm.cjs') {
        return { isGoogleLike: () => false };
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
      if (Object.prototype.hasOwnProperty.call(stubs, id)) {
        return stubs[id];
      }
      throw new Error(`Unexpected require: ${id}`);
    },
  };

  vm.runInNewContext(patch, sandbox);
  return sandbox.exports;
}

const formatExports = loadPatch('format.cjs');
const { ensureThinkingBlockInMessages, formatAgentMessages, formatLangChainMessages, formatMessage } =
  formatExports;

assert.doesNotThrow(() => formatMessage({ message: undefined, langChain: true }));

const formattedLangChain = formatLangChainMessages(
  [undefined, null, { role: 'user', content: 'Hoe voeg ik een gebruiker toe?' }],
  {},
);
assert.equal(formattedLangChain.length, 1);
assert.equal(formattedLangChain[0].role, 'user');

const { messages } = formatAgentMessages(
  [
    undefined,
    null,
    { role: 'user', content: 'Hoe voeg ik een gebruiker toe?' },
    {
      role: 'assistant',
      content: [undefined, { type: 'tool_call', tool_call: { id: 'tc_1', name: 'lookup', args: '{}' } }],
    },
    { role: 'tool', content: 'result' },
  ],
  undefined,
  new Set(['lookup']),
);
assert.equal(messages.length, 4);
assert.equal(messages[0].role, 'user');

const guarded = ensureThinkingBlockInMessages(
  [
    undefined,
    { role: 'user', content: 'first' },
    { role: 'assistant', content: [{ type: 'tool_use', name: 'lookup', input: {} }] },
    { role: 'tool', content: 'result' },
  ],
  'openai',
);
assert.ok(Array.isArray(guarded));

const streamExports = loadPatch('stream.cjs');
const { createContentAggregator, extractKlaiSourcesFromText, getChunkSources } = streamExports;
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

console.log('OK: LibreChat getklai v0.8.7 canary patches keep Klai guards and source metadata.');
