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
    // LangChain messages always carry additional_kwargs; v0.8.8 reads it
    // unguarded when it inspects the last formatted message.
    this.additional_kwargs = {};
    Object.assign(this, fields);
  }
}

class HumanMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'user';
  }

  getType() {
    return 'human';
  }
}

class AIMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'assistant';
  }

  getType() {
    return 'ai';
  }
}

class AIMessageChunk extends AIMessage {}

class SystemMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'system';
  }

  getType() {
    return 'system';
  }
}

class ToolMessage extends BaseMessage {
  constructor(fields = {}) {
    super(fields);
    this.role = 'tool';
  }

  getType() {
    return 'tool';
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
    if (id === '../common/constants.cjs') {
      // Read at load time only to derive head/tail limits; the values are
      // irrelevant to the guards under test.
      return {
        COMPACTION_SEMANTIC_INDEX_LIMITS: {
          maxInputEntries: 64,
          maxInputTextChars: 1024,
          maxIdentityChars: 256,
          maxSourceContentIndex: 1024,
        },
      };
    }
    if (id === '../common/index.cjs') {
      return {};
    }
    // Sibling modules the v0.8.8 bundle requires. The guards under test only
    // see malformed entries, so every helper behaves as "nothing special about
    // this message": no provenance, no provider tool parts, no legacy content.
    if (id === '../utils/truncation.cjs') {
      return { HARD_MAX_TOOL_RESULT_CHARS: 1_000_000 };
    }
    if (id === '../utils/toolContent.cjs') {
      return {
        compactToolContent: (content) => ({ content }),
        getToolContentCharLength: (content) => String(content).length,
        isAtomicToolContentBlock: () => false,
        serializeStructuredValueBounded: (value) => ({ content: JSON.stringify(value) }),
      };
    }
    if (id === './provenance.cjs') {
      return {
        hasBijectiveProviderContentPartMapping: () => false,
        inspectProviderMessageProvenance: () => ({ status: 'absent' }),
        inspectProviderSourceMessageIds: () => ({ status: 'absent' }),
        setFreshProviderMessageProvenance() {},
        setInvalidProviderMessageProvenance() {},
        setProviderMessageProvenance() {},
      };
    }
    if (id === './reasoningTypes.cjs') {
      return { isReasoningContentBlock: () => false };
    }
    if (id === './toolResultTypes.cjs') {
      return {
        appendProviderToolCallDescriptor() {},
        consumeProviderToolResultPair: () => false,
        getBoundedProviderPairingArrayProperty: (message, key) => message[key],
        getProviderToolCallPartDescriptor: () => undefined,
        getProviderToolResultPartDescriptor: () => undefined,
        hasStructurallyValidAnthropicWebSearchResultContent: () => false,
        isProviderToolContentPart: () => false,
      };
    }
    if (id === './toolHistoryProjection.cjs') {
      return {
        createToolHistoryPreparation: () => ({ get: () => undefined }),
        isToolHistoryCallMirror: () => false,
        recordToolHistoryCallMirror() {},
      };
    }
    if (id === '../summarization/semanticIndex.cjs') {
      return {
        snapshotCompactionSemanticIndex: (entries) => entries,
        getCompactionSemanticIndexProvidedEntryCount: () => 0,
        setCompactionSemanticIndexProvidedEntryCount() {},
      };
    }
    if (id === './content.cjs') {
      return { isLegacyConvertible: () => false, flattenLegacyContent: (content) => content };
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
    new HumanMessage({ content: 'first' }),
    new AIMessage({ content: [{ type: 'tool_use', name: 'lookup', input: {} }] }),
    new ToolMessage({ content: 'result' }),
  ],
  'openai',
);
assert.ok(Array.isArray(guarded));

// Adversarial review 2026-08-13, finding 8: sparse entries AFTER the last
// user message used to throw ("role" in undefined) because both scans lacked
// the record guard. Cover before / between / after positions explicitly.
const sparseAfter = ensureThinkingBlockInMessages(
  [{ role: 'user', content: 'vraag' }, undefined, { role: 'assistant', content: 'antwoord' }],
  'openai',
);
assert.ok(Array.isArray(sparseAfter));

const sparseBetween = ensureThinkingBlockInMessages(
  [
    { role: 'user', content: 'vraag' },
    { role: 'assistant', content: 'a1' },
    null,
    { role: 'assistant', content: 'a2' },
  ],
  'openai',
);
assert.ok(Array.isArray(sparseBetween));

const sparseEverywhere = ensureThinkingBlockInMessages(
  [undefined, { role: 'user', content: 'vraag' }, null, { role: 'assistant', content: 'a' }, undefined],
  'openai',
);
assert.ok(Array.isArray(sparseEverywhere));

console.log('OK: LibreChat format guards tolerate sparse message arrays.');
