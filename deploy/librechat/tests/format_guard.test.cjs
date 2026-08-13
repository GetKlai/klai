const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const repoRoot = path.resolve(__dirname, '../../..');
const formatPatch = fs.readFileSync(
  path.join(repoRoot, 'deploy/librechat/patches/format.cjs'),
  'utf8',
);

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

const sandbox = {
  require: (id) => {
    if (id === '@langchain/core/messages') {
      return { HumanMessage, AIMessage, AIMessageChunk, SystemMessage, ToolMessage };
    }
    if (id === '../common/enum.cjs') {
      return {
        Providers: { ANTHROPIC: 'anthropic' },
        ContentTypes: {
          TEXT: 'text',
          TOOL_CALL: 'tool_call',
          THINK: 'think',
          THINKING: 'thinking',
          REASONING: 'reasoning',
          REASONING_CONTENT: 'reasoning_content',
          ERROR: 'error',
          AGENT_UPDATE: 'agent_update',
          IMAGE_URL: 'image_url',
          SUMMARY: 'summary',
        },
        Constants: {
          TOOL_SEARCH: 'tool_search',
        },
      };
    }
    if (id === '../common/index.cjs') {
      return {};
    }
    if (id === '../utils/events.cjs') {
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
    throw new Error(`Unexpected require: ${id}`);
  },
  exports: {},
};

vm.runInNewContext(formatPatch, sandbox);

const { ensureThinkingBlockInMessages, formatAgentMessages, formatLangChainMessages, formatMessage } =
  sandbox.exports;

assert.doesNotThrow(() => formatMessage({ message: undefined, langChain: true }));

const formattedLangChain = formatLangChainMessages(
  [
    undefined,
    null,
    { role: 'user', content: 'Hoe voeg ik een gebruiker toe?' },
  ],
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

console.log('OK: LibreChat format guards tolerate sparse message arrays.');
