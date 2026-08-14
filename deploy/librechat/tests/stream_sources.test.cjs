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
    if (id === './common/index.cjs' || id === './messages/index.cjs') {
      return {};
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
    if (id === './utils/llm.cjs') {
      return { isGoogleLike: () => false };
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

// ---------------------------------------------------------------------------
// Split-marker framing (adversarial review 2026-08-13, finding 1): a
// klai_sources marker divided across stream chunks must neither leak marker
// text into the answer nor lose the sources.
// ---------------------------------------------------------------------------
const { createKlaiSourcesFramer, findPartialKlaiMarkerStart } = sandbox.exports;
assert.equal(typeof createKlaiSourcesFramer, 'function');
assert.equal(typeof findPartialKlaiMarkerStart, 'function');

const fullMarker = `<!-- klai_sources=${marker} -->`;
const splitBody = `Antwoord. ${fullMarker} En verder.`;

function runAggregator(fragments) {
  const agg = createContentAggregator();
  agg.aggregateContent({
    event: 'on_run_step',
    data: { id: 'step-split', index: 0, stepDetails: { type: 'message_creation' } },
  });
  for (const fragment of fragments) {
    agg.aggregateContent({
      event: 'on_message_delta',
      data: { id: 'step-split', delta: { content: { type: 'text', text: fragment } } },
    });
  }
  return agg.contentParts[0];
}

// Exhaustive two-fragment sweep: split the message at EVERY position.
for (let cut = 1; cut < splitBody.length; cut++) {
  const part = runAggregator([splitBody.slice(0, cut), splitBody.slice(cut)]);
  assert.equal(
    part.text,
    'Antwoord.  En verder.',
    `aggregated text leaked marker content at cut=${cut}: ${JSON.stringify(part.text)}`,
  );
  assert.equal(
    JSON.stringify(part.sources),
    JSON.stringify(sources),
    `sources lost at cut=${cut}`,
  );
}

// Three-way split through the base64 body.
{
  const a = splitBody.slice(0, 30);
  const b = splitBody.slice(30, 60);
  const c = splitBody.slice(60);
  const part = runAggregator([a, b, c]);
  assert.equal(part.text, 'Antwoord.  En verder.');
  assert.equal(JSON.stringify(part.sources), JSON.stringify(sources));
}

// A stream that ENDS mid-marker must keep the characters as plain text —
// never swallow user content (the aggregator is the persisted message).
{
  const truncated = `Antwoord. ${fullMarker.slice(0, 40)}`;
  const part = runAggregator(['Antwoord. ', fullMarker.slice(0, 40)]);
  assert.equal(part.text, truncated, 'truncated marker tail must remain as text');
  assert.equal(part.sources, undefined);
}

// A '<' that is NOT a marker prefix must pass through immediately.
{
  const part = runAggregator(['a < b en <div> blijven ', 'gewoon staan.']);
  assert.equal(part.text, 'a < b en <div> blijven gewoon staan.');
}

// Two interleaved aggregators must not cross-contaminate.
{
  const aggA = createContentAggregator();
  const aggB = createContentAggregator();
  for (const agg of [aggA, aggB]) {
    agg.aggregateContent({
      event: 'on_run_step',
      data: { id: 's', index: 0, stepDetails: { type: 'message_creation' } },
    });
  }
  aggA.aggregateContent({ event: 'on_message_delta', data: { id: 's', delta: { content: { type: 'text', text: `A. ${fullMarker.slice(0, 25)}` } } } });
  aggB.aggregateContent({ event: 'on_message_delta', data: { id: 's', delta: { content: { type: 'text', text: 'B zonder marker. ' } } } });
  aggA.aggregateContent({ event: 'on_message_delta', data: { id: 's', delta: { content: { type: 'text', text: `${fullMarker.slice(25)} klaar.` } } } });
  aggB.aggregateContent({ event: 'on_message_delta', data: { id: 's', delta: { content: { type: 'text', text: 'B einde.' } } } });
  assert.equal(aggA.contentParts[0].text, 'A.  klaar.');
  assert.equal(JSON.stringify(aggA.contentParts[0].sources), JSON.stringify(sources));
  assert.equal(aggB.contentParts[0].text, 'B zonder marker. B einde.');
  assert.equal(aggB.contentParts[0].sources, undefined);
}

// The per-run framer (live-dispatch path): exhaustive sweep as well.
for (let cut = 1; cut < splitBody.length; cut++) {
  const framer = createKlaiSourcesFramer();
  const first = framer.push(splitBody.slice(0, cut));
  const second = framer.push(splitBody.slice(cut));
  const emitted = first.text + second.text;
  const seen = first.sources ?? second.sources;
  assert.equal(emitted, 'Antwoord.  En verder.', `framer leaked at cut=${cut}: ${JSON.stringify(emitted)}`);
  assert.equal(JSON.stringify(seen), JSON.stringify(sources), `framer lost sources at cut=${cut}`);
}

// Framer overflow: a never-closing marker-lookalike longer than the cap is
// flushed as plain text instead of buffering forever.
{
  const framer = createKlaiSourcesFramer();
  // Longer than every cap this file may run against: 8192 in the .cjs still
  // bind-mounted in production, 65536 in the CI-built artifact (the cap was
  // raised because a legitimately large marker split past 8192 leaked in full
  // -- adversarial review 2026-08-14, finding 6). Sizing above both keeps this
  // assertion true for either artifact instead of silently pinning one.
  const hugeTail = `<!-- klai_sources=${'A'.repeat(70000)}`;
  const out = framer.push(`tekst ${hugeTail}`);
  assert.ok(out.text.includes(hugeTail), 'oversized tail must flush as text');
}

console.log('OK: klai_sources markers survive arbitrary stream-chunk splits without leaking or losing sources.');
