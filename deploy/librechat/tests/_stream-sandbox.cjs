/**
 * Shared sandbox for loading the bundled `@librechat/agents` stream.cjs.
 *
 * The bundle requires a handful of sibling chunks that these tests never
 * exercise. Stubbing them here, once, keeps stream_sources.test.cjs (which
 * guards the currently bind-mounted patch) and built_artifacts.test.cjs (which
 * guards the CI-built artifact) loading the module identically — otherwise the
 * two harnesses drift and a difference in test outcome could come from the
 * stub rather than from the code.
 *
 * An unexpected require throws rather than returning a permissive stub: a
 * silently-satisfied dependency is how a load-time regression hides.
 */
const vm = require('node:vm');

function makeRequire() {
  return (id) => {
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
    if (
      id === './common/index.cjs' ||
      id === './messages/index.cjs' ||
      id === './tools/handlers.cjs' ||
      id === './messages/core.cjs' ||
      id === './messages/ids.cjs' ||
      id === '@langchain/core/messages' ||
      id === './utils/truncation.cjs' ||
      id === 'uuid' ||
      id === './tools/eagerEventExecution.cjs' ||
      id === './tools/streamedToolCallSeals.cjs'
    ) {
      return {};
    }
    if (id === './utils/llm.cjs') {
      return { isGoogleLike: () => false };
    }
    if (id === './utils/events.cjs') {
      return { emitAgentLog() {} };
    }
    if (id === './tools/toolOutputReferences.cjs') {
      return { TOOL_OUTPUT_REF_PATTERN: /\{\{tool_output:[^}]+\}\}/ };
    }
    throw new Error(`Unexpected require: ${id}`);
  };
}

/**
 * @param {string} source Contents of a bundled stream.cjs.
 * @returns {Record<string, unknown>} its exports
 */
function loadStreamBundle(source) {
  const sandbox = {
    Buffer,
    require: makeRequire(),
    console,
    exports: {},
  };
  vm.runInNewContext(source, sandbox);
  return sandbox.exports;
}

module.exports = { loadStreamBundle };
