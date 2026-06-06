'use strict';

var _enum = require('./common/enum.cjs');
var handlers = require('./tools/handlers.cjs');
require('./messages/core.cjs');
var ids = require('./messages/ids.cjs');
require('@langchain/core/messages');
var truncation = require('./utils/truncation.cjs');
var events = require('./utils/events.cjs');
require('uuid');
var eagerEventExecution = require('./tools/eagerEventExecution.cjs');
var streamedToolCallSeals = require('./tools/streamedToolCallSeals.cjs');
var toolOutputReferences = require('./tools/toolOutputReferences.cjs');

const LOCAL_CODING_BUNDLE_NAME_SET = new Set(_enum.LOCAL_CODING_BUNDLE_NAMES);
/**
 * Parses content to extract thinking sections enclosed in <think> tags using string operations
 * @param content The content to parse
 * @returns An object with separated text and thinking content
 */
function parseThinkingContent(content) {
    // If no think tags, return the original content as text
    if (!content.includes('<think>')) {
        return { text: content, thinking: '' };
    }
    let textResult = '';
    const thinkingResult = [];
    let position = 0;
    while (position < content.length) {
        const thinkStart = content.indexOf('<think>', position);
        if (thinkStart === -1) {
            // No more think tags, add the rest and break
            textResult += content.slice(position);
            break;
        }
        // Add text before the think tag
        textResult += content.slice(position, thinkStart);
        const thinkEnd = content.indexOf('</think>', thinkStart);
        if (thinkEnd === -1) {
            // Malformed input, no closing tag
            textResult += content.slice(thinkStart);
            break;
        }
        // Add the thinking content
        const thinkContent = content.slice(thinkStart + 7, thinkEnd);
        thinkingResult.push(thinkContent);
        // Move position to after the think tag
        position = thinkEnd + 8; // 8 is the length of '</think>'
    }
    return {
        text: textResult.trim(),
        thinking: thinkingResult.join('\n').trim(),
    };
}
function getNonEmptyValue(possibleValues) {
    for (const value of possibleValues) {
        if (value && value.trim() !== '') {
            return value;
        }
    }
    return undefined;
}
function isBatchSensitiveToolExecution(graph) {
    return graph.hookRegistry != null || graph.humanInTheLoop?.enabled === true;
}
function hasToolOutputReference(value) {
    if (typeof value === 'string') {
        return toolOutputReferences.TOOL_OUTPUT_REF_PATTERN.test(value);
    }
    if (Array.isArray(value)) {
        return value.some((item) => hasToolOutputReference(item));
    }
    if (value !== null && typeof value === 'object') {
        return Object.values(value).some((item) => hasToolOutputReference(item));
    }
    return false;
}
function isDirectGraphTool(name, agentContext) {
    if (name.startsWith(_enum.Constants.LC_TRANSFER_TO_)) {
        return true;
    }
    return (agentContext?.graphTools?.some((tool) => 'name' in tool && tool.name === name) === true);
}
function isDirectLocalTool(name, graph) {
    const toolExecution = graph.toolExecution;
    const engine = toolExecution?.engine;
    if (toolExecution == null ||
        (engine !== 'local' && engine !== 'cloudflare-sandbox')) {
        return false;
    }
    const includeCodingTools = engine === 'cloudflare-sandbox'
        ? toolExecution.cloudflare?.includeCodingTools
        : toolExecution.local?.includeCodingTools;
    if (includeCodingTools === false) {
        return _enum.CODE_EXECUTION_TOOLS.has(name);
    }
    return LOCAL_CODING_BUNDLE_NAME_SET.has(name);
}
function toCodeEnvFile(file, execSessionId) {
    const base = {
        id: file.id,
        resource_id: file.resource_id ?? file.id,
        name: file.name,
        storage_session_id: file.storage_session_id ?? execSessionId,
    };
    const kind = file.kind ?? 'user';
    if (kind === 'skill' && file.version != null) {
        return { ...base, kind: 'skill', version: file.version };
    }
    if (kind === 'agent') {
        return { ...base, kind: 'agent' };
    }
    return { ...base, kind: 'user' };
}
function getCodeSessionContext(graph, name) {
    if (!_enum.CODE_EXECUTION_TOOLS.has(name) &&
        name !== _enum.Constants.SKILL_TOOL &&
        name !== _enum.Constants.READ_FILE) {
        return undefined;
    }
    const codeSession = graph.sessions.get(_enum.Constants.EXECUTE_CODE);
    if (codeSession?.session_id == null || codeSession.session_id === '') {
        return undefined;
    }
    return {
        session_id: codeSession.session_id,
        files: codeSession.files?.map((file) => toCodeEnvFile(file, codeSession.session_id)),
    };
}
function isEagerToolExecutionEnabledForBatch(args) {
    const { graph, metadata, agentContext } = args;
    if (graph.eagerEventToolExecution?.enabled !== true) {
        return false;
    }
    if ((agentContext?.toolDefinitions?.length ?? 0) === 0) {
        return false;
    }
    if (isBatchSensitiveToolExecution(graph)) {
        return false;
    }
    if (metadata?.[_enum.Constants.PROGRAMMATIC_TOOL_CALLING] === true ||
        metadata?.[_enum.Constants.BASH_PROGRAMMATIC_TOOL_CALLING] === true) {
        return false;
    }
    if (graph.handlerRegistry?.getHandler(_enum.GraphEvents.ON_TOOL_EXECUTE) == null &&
        graph.eventToolExecutionAvailable !== true) {
        return false;
    }
    return true;
}
function hasFinalToolCallSignal(chunk) {
    const metadata = chunk.response_metadata;
    const finishReason = metadata?.finish_reason ??
        metadata?.finishReason ??
        metadata?.stop_reason ??
        metadata?.stopReason;
    return finishReason === 'tool_calls' || finishReason === 'tool_use';
}
function canPrestartSequentialStreamedToolChunks(agentContext) {
    // Anthropic seals each prior streamed tool-use block when the next indexed
    // tool-use block begins. Live Kimi/Moonshot streams can still revise prior
    // args after advancing to the next index, so keep those on the final
    // tool-call path unless they grow an explicit adapter seal.
    return agentContext?.provider === _enum.Providers.ANTHROPIC;
}
function hasExplicitStreamedToolCallSeals(chunk) {
    return (streamedToolCallSeals.getStreamedToolCallAdapter(chunk.response_metadata) != null);
}
function hasDirectToolCallInBatch(args) {
    const { graph, agentContext, toolCalls } = args;
    return toolCalls.some((toolCall) => toolCall.name !== '' &&
        (isDirectGraphTool(toolCall.name, agentContext) ||
            isDirectLocalTool(toolCall.name, graph)));
}
function hasPotentialDirectToolInStreamContext(args) {
    const { graph, agentContext } = args;
    const engine = graph.toolExecution?.engine;
    if (engine === 'local' || engine === 'cloudflare-sandbox') {
        return true;
    }
    if ((agentContext?.graphTools?.length ?? 0) > 0) {
        return true;
    }
    return false;
}
function hasDirectToolCallChunkInBatch(args) {
    const { graph, agentContext, toolCallChunks } = args;
    return (toolCallChunks?.some((toolCallChunk) => toolCallChunk.name != null &&
        toolCallChunk.name !== '' &&
        (isDirectGraphTool(toolCallChunk.name, agentContext) ||
            isDirectLocalTool(toolCallChunk.name, graph))) === true);
}
function hasDirectToolCallChunkStateInStep(args) {
    const { graph, agentContext, stepKey } = args;
    const prefix = `${stepKey}\u0000`;
    for (const [key, state] of graph.eagerEventToolCallChunks) {
        if (!key.startsWith(prefix)) {
            continue;
        }
        const name = state.name;
        if (name != null &&
            name !== '' &&
            (isDirectGraphTool(name, agentContext) || isDirectLocalTool(name, graph))) {
            return true;
        }
    }
    return false;
}
function createEagerToolExecutionPlan(args) {
    const { graph, metadata, agentContext, toolCalls, skipExisting = false, } = args;
    if (!isEagerToolExecutionEnabledForBatch({
        graph,
        metadata,
        agentContext,
    })) {
        return undefined;
    }
    if (hasDirectToolCallInBatch({ graph, agentContext, toolCalls })) {
        return undefined;
    }
    if (graph.toolOutputReferences?.enabled === true &&
        toolCalls.some((toolCall) => hasToolOutputReference(toolCall.args))) {
        return undefined;
    }
    const candidateToolCalls = skipExisting
        ? toolCalls.filter((toolCall) => {
            if (toolCall.id == null || toolCall.id === '') {
                return true;
            }
            return !graph.eagerEventToolExecutions.has(toolCall.id);
        })
        : toolCalls;
    if (candidateToolCalls.length === 0) {
        return [];
    }
    // Eager execution must preserve ToolNode batch semantics exactly for every
    // unstarted call. If any candidate cannot be planned, fall back for that
    // candidate set.
    if (candidateToolCalls.some((toolCall) => toolCall.id == null ||
        toolCall.id === '' ||
        toolCall.name === '' ||
        (!skipExisting && graph.eagerEventToolExecutions.has(toolCall.id)))) {
        return undefined;
    }
    const plan = eagerEventExecution.buildToolExecutionRequestPlan({
        toolCalls: candidateToolCalls.map((toolCall) => ({
            id: toolCall.id,
            name: toolCall.name,
            args: toolCall.args,
            stepId: graph.toolCallStepIds.get(toolCall.id) ?? '',
            codeSessionContext: getCodeSessionContext(graph, toolCall.name),
        })),
        usageCount: graph.getEagerEventToolUsageCount(agentContext?.agentId),
    });
    if (plan == null) {
        return undefined;
    }
    return plan.requests.map((request) => ({
        id: request.id,
        toolName: request.name,
        coercedArgs: request.args,
        request,
    }));
}
function startEagerToolExecutions(args) {
    const { graph, metadata, agentContext, toolCalls, skipExisting } = args;
    const entries = createEagerToolExecutionPlan({
        graph,
        metadata,
        agentContext,
        toolCalls,
        skipExisting,
    });
    if (entries == null || entries.length === 0) {
        return;
    }
    const records = [];
    const promise = new Promise((resolve, reject) => {
        let dispatchSettled = false;
        let resultSettled = false;
        let settledResults;
        const maybeResolve = () => {
            if (dispatchSettled && resultSettled) {
                resolve(settledResults ?? []);
            }
        };
        const batchRequest = {
            toolCalls: entries.map((entry) => entry.request),
            userId: graph.config?.configurable?.user_id,
            agentId: agentContext?.agentId,
            configurable: graph.config?.configurable,
            metadata,
            resolve: (results) => {
                resultSettled = true;
                settledResults = results;
                maybeResolve();
            },
            reject,
        };
        void events.safeDispatchCustomEvent(_enum.GraphEvents.ON_TOOL_EXECUTE, batchRequest, graph.config)
            .then(() => {
            dispatchSettled = true;
            maybeResolve();
        })
            .catch(reject);
    }).then(async (results) => {
        await dispatchEagerToolCompletions({
            graph,
            agentContext,
            records,
            results,
        });
        return { results };
    }, (error) => ({
        error: eagerEventExecution.normalizeError(error),
    }));
    for (const entry of entries) {
        const record = {
            toolCallId: entry.id,
            toolName: entry.toolName,
            args: entry.coercedArgs,
            request: entry.request,
            promise,
        };
        records.push(record);
        graph.eagerEventToolExecutions.set(entry.id, record);
    }
}
async function dispatchEagerToolCompletions(args) {
    const { graph, agentContext, records, results } = args;
    const recordById = new Map(records.map((record) => [record.toolCallId, record]));
    const maxToolResultChars = agentContext?.maxToolResultChars ??
        truncation.calculateMaxToolResultChars(agentContext?.maxContextTokens);
    for (const result of results) {
        const record = recordById.get(result.toolCallId);
        if (record == null) {
            continue;
        }
        if (graph.eagerEventToolExecutions.get(result.toolCallId) !== record) {
            continue;
        }
        const stepId = record.request.stepId ??
            graph.toolCallStepIds.get(result.toolCallId) ??
            '';
        if (stepId === '') {
            continue;
        }
        const output = result.status === 'error'
            ? `Error: ${result.errorMessage ?? 'Unknown error'}\n Please fix your mistakes.`
            : truncation.truncateToolResultContent(typeof result.content === 'string'
                ? result.content
                : JSON.stringify(result.content), maxToolResultChars);
        try {
            const dispatched = await events.safeDispatchCustomEvent(_enum.GraphEvents.ON_RUN_STEP_COMPLETED, {
                result: {
                    id: stepId,
                    index: record.request.turn ?? 0,
                    type: 'tool_call',
                    eager: true,
                    tool_call: {
                        args: JSON.stringify(record.request.args),
                        name: record.toolName,
                        id: result.toolCallId,
                        output,
                        progress: 1,
                    },
                },
            }, graph.config);
            if (dispatched === false) {
                continue;
            }
            record.completionDispatched = true;
        }
        catch (error) {
            // Let ToolNode dispatch the completion through the normal path later.
            console.warn(`[stream] eager completion dispatch failed for toolCallId=${result.toolCallId}:`, error instanceof Error ? error.message : error);
        }
    }
}
function getEagerToolChunkKey(stepKey, toolCallChunk) {
    let chunkKey;
    if (typeof toolCallChunk.index === 'number') {
        chunkKey = String(toolCallChunk.index);
    }
    else if (toolCallChunk.id != null && toolCallChunk.id !== '') {
        chunkKey = toolCallChunk.id;
    }
    if (chunkKey == null) {
        return undefined;
    }
    return `${stepKey}\u0000${chunkKey}`;
}
function getEagerToolChunkIndex(toolCallChunk) {
    return typeof toolCallChunk.index === 'number'
        ? toolCallChunk.index
        : undefined;
}
function pruneEagerToolCallChunkStates(args) {
    const { graph, stepKey, toolCallIds, clearStep = false } = args;
    const prefix = `${stepKey}\u0000`;
    for (const [key, state] of graph.eagerEventToolCallChunks) {
        if (!key.startsWith(prefix)) {
            continue;
        }
        if (clearStep ||
            (state.id != null && toolCallIds?.has(state.id) === true)) {
            graph.eagerEventToolCallChunks.delete(key);
        }
    }
}
function isEagerToolChunkStateComplete(state) {
    return (state.id != null &&
        state.id !== '' &&
        state.name != null &&
        state.name !== '' &&
        eagerEventExecution.coerceRecordArgs(state.argsText) != null);
}
function mergeToolCallArgsText(existing, incoming) {
    if (incoming === '') {
        return existing;
    }
    if (existing === '') {
        return incoming;
    }
    if (incoming === existing) {
        try {
            JSON.parse(incoming);
            return incoming;
        }
        catch {
            return `${existing}${incoming}`;
        }
    }
    if (incoming.startsWith(existing)) {
        return incoming;
    }
    if (existing.startsWith(incoming)) {
        return existing;
    }
    try {
        JSON.parse(existing);
        JSON.parse(incoming);
        return incoming;
    }
    catch {
        // Fall through to delta concatenation.
    }
    for (let overlap = Math.min(existing.length, incoming.length); overlap >= 8; overlap -= 1) {
        if (existing.endsWith(incoming.slice(0, overlap))) {
            return `${existing}${incoming.slice(overlap)}`;
        }
    }
    return `${existing}${incoming}`;
}
function recordEagerToolCallChunks(args) {
    const { graph, stepKey, toolCallChunks } = args;
    if (toolCallChunks == null || toolCallChunks.length === 0) {
        return;
    }
    // Streamed args can be cumulative and parseable before the provider has
    // sealed the call. Recording stays separate from dispatch so the boundary
    // logic can wait for either a later tool index or the final tool-call signal.
    for (const toolCallChunk of toolCallChunks) {
        const key = getEagerToolChunkKey(stepKey, toolCallChunk);
        if (key == null) {
            continue;
        }
        const incomingId = toolCallChunk.id != null && toolCallChunk.id !== ''
            ? toolCallChunk.id
            : undefined;
        const incomingName = toolCallChunk.name != null && toolCallChunk.name !== ''
            ? toolCallChunk.name
            : undefined;
        const previous = graph.eagerEventToolCallChunks.get(key);
        const shouldReset = previous != null &&
            ((incomingId != null &&
                previous.id != null &&
                incomingId !== previous.id) ||
                (incomingName != null &&
                    previous.name != null &&
                    incomingName !== previous.name));
        const existing = previous == null || shouldReset
            ? {
                argsText: '',
            }
            : previous;
        const id = incomingId ?? existing.id;
        const name = incomingName ?? existing.name;
        const incomingArgs = toolCallChunk.args ?? '';
        const isRepeatedObservedFragment = incomingArgs !== '' &&
            incomingArgs.length > 1 &&
            incomingArgs === existing.lastArgsFragment;
        const argsText = isRepeatedObservedFragment
            ? existing.argsText
            : mergeToolCallArgsText(existing.argsText, incomingArgs);
        const next = {
            id,
            name,
            argsText,
            index: getEagerToolChunkIndex(toolCallChunk) ?? existing.index,
            lastArgsFragment: incomingArgs !== '' ? incomingArgs : existing.lastArgsFragment,
        };
        graph.eagerEventToolCallChunks.set(key, next);
    }
}
function getStreamedReadyToolCalls(args) {
    const { graph, stepKey, toolCallChunks, seal, allowSequentialSeal = false, sealAll = false, } = args;
    const currentIndices = new Set();
    for (const toolCallChunk of toolCallChunks ?? []) {
        const index = getEagerToolChunkIndex(toolCallChunk);
        if (index != null) {
            currentIndices.add(index);
        }
    }
    const highestCurrentIndex = currentIndices.size > 0 ? Math.max(...currentIndices) : undefined;
    const prefix = `${stepKey}\u0000`;
    const readyEntries = [];
    for (const [key, state] of graph.eagerEventToolCallChunks) {
        if (!key.startsWith(prefix)) {
            continue;
        }
        if (state.id != null && graph.eagerEventToolExecutions.has(state.id)) {
            graph.eagerEventToolCallChunks.delete(key);
            continue;
        }
        if (!isEagerToolChunkStateComplete(state)) {
            continue;
        }
        const isSealedByLaterChunk = allowSequentialSeal &&
            highestCurrentIndex != null &&
            state.index != null &&
            state.index < highestCurrentIndex &&
            !currentIndices.has(state.index);
        const isSealedExplicitly = seal?.kind === 'single' &&
            ((seal.id != null && state.id === seal.id) ||
                (seal.index != null && state.index === seal.index));
        if (sealAll ||
            seal?.kind === 'all' ||
            isSealedByLaterChunk ||
            isSealedExplicitly) {
            readyEntries.push({ key, state });
        }
    }
    pruneEagerToolCallChunkStates({
        graph,
        stepKey,
        toolCallIds: new Set(readyEntries
            .map(({ state }) => state.id)
            .filter((id) => id != null && id !== '')),
    });
    if (sealAll) {
        pruneEagerToolCallChunkStates({ graph, stepKey, clearStep: true });
    }
    return readyEntries
        .sort((left, right) => (left.state.index ?? 0) - (right.state.index ?? 0))
        .flatMap(({ state }) => {
        const args = eagerEventExecution.coerceRecordArgs(state.argsText);
        if (args == null) {
            return [];
        }
        return [
            {
                id: state.id,
                name: state.name ?? '',
                args,
            },
        ];
    });
}
function startReadyStreamedEagerToolExecutions(args) {
    const { graph, metadata, agentContext, stepKey, toolCallChunks, seal, allowSequentialSeal, sealAll, } = args;
    if (hasPotentialDirectToolInStreamContext({ graph, agentContext }) ||
        hasDirectToolCallChunkInBatch({ graph, agentContext, toolCallChunks }) ||
        hasDirectToolCallChunkStateInStep({ graph, agentContext, stepKey }) ||
        !isEagerToolExecutionEnabledForBatch({ graph, metadata, agentContext })) {
        return;
    }
    const toolCalls = getStreamedReadyToolCalls({
        graph,
        stepKey,
        toolCallChunks,
        seal,
        allowSequentialSeal,
        sealAll,
    });
    if (toolCalls.length === 0) {
        return;
    }
    startEagerToolExecutions({
        graph,
        metadata,
        agentContext,
        toolCalls,
        skipExisting: true,
    });
}

function getChunkSources(chunk) {
    const candidates = [
        chunk?.sources,
        chunk?.additional_kwargs?.sources,
        chunk?.response_metadata?.sources,
        chunk?.additional_kwargs?.delta?.sources,
    ];
    for (const candidate of candidates) {
        if (Array.isArray(candidate) && candidate.length > 0) {
            return candidate;
        }
    }
    return undefined;
}
const KLAI_SOURCES_MARKER_RE = /<!--\s*klai_sources=([A-Za-z0-9_-]+={0,2})\s*-->/g;
function decodeKlaiSourcesMarker(encoded) {
    try {
        const padded = encoded + '='.repeat((4 - (encoded.length % 4)) % 4);
        const json = Buffer.from(padded.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
        const parsed = JSON.parse(json);
        return Array.isArray(parsed) ? parsed : undefined;
    }
    catch (_error) {
        return undefined;
    }
}
function extractKlaiSourcesFromText(text) {
    let sources;
    const cleanText = text.replace(KLAI_SOURCES_MARKER_RE, (_match, encoded) => {
        const decoded = decodeKlaiSourcesMarker(encoded);
        if (decoded && decoded.length > 0) {
            sources = decoded;
        }
        return '';
    });
    return { text: cleanText, sources };
}
function getChunkContent({ chunk, provider, reasoningKey, }) {
    if ((provider === _enum.Providers.OPENAI || provider === _enum.Providers.AZURE) &&
        chunk?.additional_kwargs?.reasoning?.summary?.[0]?.text != null &&
        (chunk?.additional_kwargs?.reasoning?.summary?.[0]?.text?.length ?? 0) > 0) {
        return chunk?.additional_kwargs?.reasoning?.summary?.[0]?.text;
    }
    /**
     * For OpenRouter, reasoning is stored in additional_kwargs.reasoning (not reasoning_content).
     * NOTE: We intentionally do NOT extract text from reasoning_details here.
     * The reasoning_details array contains the FULL accumulated reasoning text (set only on final chunk),
     * but individual reasoning tokens are already streamed via additional_kwargs.reasoning.
     * Extracting from reasoning_details would cause duplication.
     * The reasoning_details is only used for:
     * 1. Detecting reasoning mode in handleReasoning()
     * 2. Final message storage (for thought signatures)
     */
    if (provider === _enum.Providers.OPENROUTER) {
        // Content presence signals end of reasoning phase - prefer content over reasoning
        // This handles transitional chunks that may have both reasoning and content
        if (typeof chunk?.content === 'string' && chunk.content !== '') {
            return chunk.content;
        }
        const reasoning = chunk?.additional_kwargs?.reasoning;
        if (reasoning != null && reasoning !== '') {
            return reasoning;
        }
        return chunk?.content;
    }
    return ((chunk?.additional_kwargs?.[reasoningKey] ?? '') ||
        chunk?.content);
}
class ChatModelStreamHandler {
    async handle(event, data, metadata, graph) {
        if (!graph) {
            throw new Error('Graph not found');
        }
        if (!graph.config) {
            throw new Error('Config not found in graph');
        }
        if (!data.chunk) {
            console.warn(`No chunk found in ${event} event`);
            return;
        }
        const agentContext = graph.getAgentContext(metadata);
        const chunk = data.chunk;
        const content = getChunkContent({
            chunk,
            reasoningKey: agentContext.reasoningKey,
            provider: agentContext.provider,
        });
        const skipHandling = await handlers.handleServerToolResult({
            graph,
            content,
            metadata,
            agentContext,
        });
        if (skipHandling) {
            return;
        }
        this.handleReasoning(chunk, agentContext);
        const stepKey = graph.getStepKey(metadata);
        let hasToolCalls = false;
        const hasToolCallChunks = (chunk.tool_call_chunks && chunk.tool_call_chunks.length > 0) ?? false;
        if (chunk.tool_calls &&
            chunk.tool_calls.length > 0 &&
            chunk.tool_calls.every((tc) => tc.id != null &&
                tc.id !== '' &&
                tc.name != null &&
                tc.name !== '')) {
            hasToolCalls = true;
            await handlers.handleToolCalls(chunk.tool_calls, metadata, graph);
            if (hasFinalToolCallSignal(chunk)) {
                startEagerToolExecutions({
                    graph,
                    metadata,
                    agentContext,
                    toolCalls: chunk.tool_calls,
                    skipExisting: true,
                });
                if (!hasToolCallChunks) {
                    pruneEagerToolCallChunkStates({ graph, stepKey, clearStep: true });
                }
            }
        }
        const isEmptyContent = typeof content === 'undefined' ||
            !content.length ||
            (typeof content === 'string' && !content);
        /** Set a preliminary message ID if found in empty chunk */
        const isEmptyChunk = isEmptyContent && !hasToolCallChunks;
        if (isEmptyChunk &&
            (chunk.id ?? '') !== '' &&
            !graph.prelimMessageIdsByStepKey.has(chunk.id ?? '')) {
            graph.prelimMessageIdsByStepKey.set(stepKey, chunk.id ?? '');
        }
        else if (isEmptyChunk) {
            return;
        }
        if (hasToolCallChunks &&
            chunk.tool_call_chunks &&
            chunk.tool_call_chunks.length &&
            typeof chunk.tool_call_chunks[0]?.index === 'number') {
            const streamedToolCallSeal = streamedToolCallSeals.getStreamedToolCallSeal(chunk.response_metadata);
            const allowSequentialSeal = canPrestartSequentialStreamedToolChunks(agentContext);
            const canStreamEager = (allowSequentialSeal || hasExplicitStreamedToolCallSeals(chunk)) &&
                !hasPotentialDirectToolInStreamContext({ graph, agentContext }) &&
                isEagerToolExecutionEnabledForBatch({ graph, metadata, agentContext });
            if (canStreamEager) {
                recordEagerToolCallChunks({
                    graph,
                    stepKey,
                    toolCallChunks: chunk.tool_call_chunks,
                });
            }
            await handlers.handleToolCallChunks({
                graph,
                stepKey,
                toolCallChunks: chunk.tool_call_chunks,
                metadata,
            });
            if (canStreamEager) {
                startReadyStreamedEagerToolExecutions({
                    graph,
                    metadata,
                    agentContext,
                    stepKey,
                    toolCallChunks: chunk.tool_call_chunks,
                    seal: streamedToolCallSeal,
                    allowSequentialSeal,
                    sealAll: hasFinalToolCallSignal(chunk),
                });
            }
        }
        if (isEmptyContent) {
            return;
        }
        const message_id = ids.getMessageId(stepKey, graph) ?? '';
        if (message_id) {
            await graph.dispatchRunStep(stepKey, {
                type: _enum.StepTypes.MESSAGE_CREATION,
                message_creation: {
                    message_id,
                },
            }, metadata);
        }
        const stepId = graph.getStepIdByKey(stepKey);
        const runStep = graph.getRunStep(stepId);
        if (!runStep) {
            console.warn(`\n
==============================================================


Run step for ${stepId} does not exist, cannot dispatch delta event.

event: ${event}
stepId: ${stepId}
stepKey: ${stepKey}
message_id: ${message_id}
hasToolCalls: ${hasToolCalls}
hasToolCallChunks: ${hasToolCallChunks}

==============================================================
\n`);
            return;
        }
        /* Note: tool call chunks may have non-empty content that matches the current tool chunk generation */
        if (typeof content === 'string' && runStep.type === _enum.StepTypes.TOOL_CALLS) {
            return;
        }
        else if (hasToolCallChunks &&
            (chunk.tool_call_chunks?.some((tc) => tc.args === content) ?? false)) {
            return;
        }
        else if (typeof content === 'string') {
            const extracted = extractKlaiSourcesFromText(content);
            const textContent = extracted.text;
            const sources = getChunkSources(chunk) ?? extracted.sources;
            if (agentContext.currentTokenType === _enum.ContentTypes.TEXT) {
                await graph.dispatchMessageDelta(stepId, {
                    content: [
                        {
                            type: _enum.ContentTypes.TEXT,
                            text: textContent,
                            ...(sources ? { sources } : {}),
                        },
                    ],
                }, metadata);
            }
            else if (agentContext.currentTokenType === 'think_and_text') {
                const { text, thinking } = parseThinkingContent(textContent);
                if (thinking) {
                    await graph.dispatchReasoningDelta(stepId, {
                        content: [
                            {
                                type: _enum.ContentTypes.THINK,
                                think: thinking,
                            },
                        ],
                    }, metadata);
                }
                if (text) {
                    agentContext.currentTokenType = _enum.ContentTypes.TEXT;
                    agentContext.tokenTypeSwitch = 'content';
                    const newStepKey = graph.getStepKey(metadata);
                    const message_id = ids.getMessageId(newStepKey, graph) ?? '';
                    await graph.dispatchRunStep(newStepKey, {
                        type: _enum.StepTypes.MESSAGE_CREATION,
                        message_creation: {
                            message_id,
                        },
                    }, metadata);
                    const newStepId = graph.getStepIdByKey(newStepKey);
                    await graph.dispatchMessageDelta(newStepId, {
                        content: [
                            {
                                type: _enum.ContentTypes.TEXT,
                                text: text,
                                ...(sources ? { sources } : {}),
                            },
                        ],
                    }, metadata);
                }
            }
            else {
                await graph.dispatchReasoningDelta(stepId, {
                    content: [
                        {
                            type: _enum.ContentTypes.THINK,
                            think: content,
                        },
                    ],
                }, metadata);
            }
        }
        else if (content.every((c) => c.type?.startsWith(_enum.ContentTypes.TEXT) ?? false)) {
            await graph.dispatchMessageDelta(stepId, {
                content,
            }, metadata);
        }
        else if (content.every((c) => (c.type?.startsWith(_enum.ContentTypes.THINKING) ?? false) ||
            (c.type?.startsWith(_enum.ContentTypes.REASONING) ?? false) ||
            (c.type?.startsWith(_enum.ContentTypes.REASONING_CONTENT) ?? false) ||
            c.type === 'redacted_thinking')) {
            await graph.dispatchReasoningDelta(stepId, {
                content: content.map((c) => ({
                    type: _enum.ContentTypes.THINK,
                    think: c.thinking ??
                        c.reasoning ??
                        c.reasoningText
                            ?.text ??
                        '',
                })),
            }, metadata);
        }
    }
    handleReasoning(chunk, agentContext) {
        let reasoning_content = chunk.additional_kwargs?.[agentContext.reasoningKey];
        if (Array.isArray(chunk.content) &&
            (chunk.content[0]?.type === _enum.ContentTypes.THINKING ||
                chunk.content[0]?.type === _enum.ContentTypes.REASONING ||
                chunk.content[0]?.type === _enum.ContentTypes.REASONING_CONTENT ||
                chunk.content[0]?.type === 'redacted_thinking')) {
            reasoning_content = 'valid';
        }
        else if ((agentContext.provider === _enum.Providers.OPENAI ||
            agentContext.provider === _enum.Providers.AZURE) &&
            reasoning_content != null &&
            typeof reasoning_content !== 'string' &&
            reasoning_content.summary?.[0]?.text != null &&
            reasoning_content.summary[0].text) {
            reasoning_content = 'valid';
        }
        else if (agentContext.provider === _enum.Providers.OPENROUTER &&
            // Only set reasoning as valid if content is NOT present (content signals end of reasoning)
            (chunk.content == null || chunk.content === '') &&
            // Check for reasoning_details (final chunk) OR reasoning string (intermediate chunks)
            ((chunk.additional_kwargs?.reasoning_details != null &&
                Array.isArray(chunk.additional_kwargs.reasoning_details) &&
                chunk.additional_kwargs.reasoning_details.length > 0) ||
                (typeof chunk.additional_kwargs?.reasoning === 'string' &&
                    chunk.additional_kwargs.reasoning !== ''))) {
            reasoning_content = 'valid';
        }
        if (reasoning_content != null &&
            reasoning_content !== '' &&
            (chunk.content == null ||
                chunk.content === '' ||
                reasoning_content === 'valid')) {
            agentContext.currentTokenType = _enum.ContentTypes.THINK;
            agentContext.tokenTypeSwitch = 'reasoning';
            return;
        }
        else if (agentContext.tokenTypeSwitch === 'reasoning' &&
            agentContext.currentTokenType !== _enum.ContentTypes.TEXT &&
            ((chunk.content != null && chunk.content !== '') ||
                (chunk.tool_calls?.length ?? 0) > 0 ||
                (chunk.tool_call_chunks?.length ?? 0) > 0)) {
            agentContext.currentTokenType = _enum.ContentTypes.TEXT;
            agentContext.tokenTypeSwitch = 'content';
            agentContext.reasoningTransitionCount++;
        }
        else if (chunk.content != null &&
            typeof chunk.content === 'string' &&
            chunk.content.includes('<think>') &&
            chunk.content.includes('</think>')) {
            agentContext.currentTokenType = 'think_and_text';
            agentContext.tokenTypeSwitch = 'content';
        }
        else if (chunk.content != null &&
            typeof chunk.content === 'string' &&
            chunk.content.includes('<think>')) {
            agentContext.currentTokenType = _enum.ContentTypes.THINK;
            agentContext.tokenTypeSwitch = 'content';
        }
        else if (agentContext.lastToken != null &&
            agentContext.lastToken.includes('</think>')) {
            agentContext.currentTokenType = _enum.ContentTypes.TEXT;
            agentContext.tokenTypeSwitch = 'content';
        }
        if (typeof chunk.content !== 'string') {
            return;
        }
        agentContext.lastToken = chunk.content;
    }
}
function createContentAggregator() {
    const contentParts = [];
    const stepMap = new Map();
    const toolCallIdMap = new Map();
    // Track agentId and groupId for each content index (applied to content parts)
    const contentMetaMap = new Map();
    const getFirstContentPart = (content) => {
        if (content == null) {
            return undefined;
        }
        return Array.isArray(content) ? content[0] : content;
    };
    const updateContent = (index, contentPart, finalUpdate = false) => {
        if (!contentPart) {
            console.warn('No content part found in \'updateContent\'');
            return;
        }
        const partType = contentPart.type ?? '';
        if (!partType) {
            console.warn('No content type found in content part');
            return;
        }
        if (!contentParts[index] && partType !== _enum.ContentTypes.TOOL_CALL) {
            contentParts[index] = { type: partType };
        }
        if (!partType.startsWith(contentParts[index]?.type ?? '')) {
            console.warn('Content type mismatch');
            return;
        }
        if (partType.startsWith(_enum.ContentTypes.TEXT) &&
            _enum.ContentTypes.TEXT in contentPart &&
            typeof contentPart.text === 'string') {
            // TODO: update this!!
            const currentContent = contentParts[index];
            const extracted = extractKlaiSourcesFromText(contentPart.text);
            const update = {
                type: _enum.ContentTypes.TEXT,
                text: (currentContent.text || '') + extracted.text,
            };
            if (contentPart.tool_call_ids) {
                update.tool_call_ids = contentPart.tool_call_ids;
            }
            const incomingSources = contentPart.sources ?? extracted.sources;
            if (Array.isArray(incomingSources) && incomingSources.length > 0) {
                update.sources = incomingSources;
            }
            else if (Array.isArray(currentContent.sources) && currentContent.sources.length > 0) {
                update.sources = currentContent.sources;
            }
            contentParts[index] = update;
        }
        else if (partType.startsWith(_enum.ContentTypes.THINK) &&
            _enum.ContentTypes.THINK in contentPart &&
            typeof contentPart.think === 'string') {
            const currentContent = contentParts[index];
            const update = {
                type: _enum.ContentTypes.THINK,
                think: (currentContent.think || '') + contentPart.think,
            };
            contentParts[index] = update;
        }
        else if (partType.startsWith(_enum.ContentTypes.AGENT_UPDATE) &&
            _enum.ContentTypes.AGENT_UPDATE in contentPart &&
            contentPart.agent_update != null) {
            const update = {
                type: _enum.ContentTypes.AGENT_UPDATE,
                agent_update: contentPart.agent_update,
            };
            contentParts[index] = update;
        }
        else if (partType === _enum.ContentTypes.SUMMARY) {
            const currentSummary = contentParts[index];
            const incoming = contentPart;
            contentParts[index] = {
                ...incoming,
                content: [
                    ...(currentSummary?.content ?? []),
                    ...(incoming.content ?? []),
                ],
            };
        }
        else if (partType === _enum.ContentTypes.IMAGE_URL &&
            'image_url' in contentPart) {
            const currentContent = contentParts[index];
            contentParts[index] = {
                ...currentContent,
            };
        }
        else if (partType === _enum.ContentTypes.TOOL_CALL &&
            'tool_call' in contentPart) {
            const incomingName = contentPart.tool_call.name;
            const incomingId = contentPart.tool_call.id;
            const toolCallArgs = contentPart.tool_call.args;
            // When we receive a tool call with a name, it's the complete tool call
            // Consolidate with any previously accumulated args from chunks
            const hasValidName = incomingName != null && incomingName !== '';
            // Only process if incoming has a valid name (complete tool call)
            // or if we're doing a final update with complete data
            if (!hasValidName && !finalUpdate) {
                return;
            }
            const existingContent = contentParts[index];
            if (!finalUpdate && existingContent?.tool_call?.progress === 1) {
                return;
            }
            /** When args are a valid object, they are likely already invoked */
            let args = finalUpdate ||
                typeof existingContent?.tool_call?.args === 'object' ||
                typeof toolCallArgs === 'object'
                ? contentPart.tool_call.args
                : (existingContent?.tool_call?.args ?? '') + (toolCallArgs ?? '');
            if (finalUpdate &&
                args == null &&
                existingContent?.tool_call?.args != null) {
                args = existingContent.tool_call.args;
            }
            const id = getNonEmptyValue([incomingId, existingContent?.tool_call?.id]) ?? '';
            const name = getNonEmptyValue([incomingName, existingContent?.tool_call?.name]) ??
                '';
            const newToolCall = {
                id,
                name,
                args,
                type: _enum.ToolCallTypes.TOOL_CALL,
            };
            const auth = contentPart.tool_call.auth ?? existingContent?.tool_call?.auth;
            const expiresAt = contentPart.tool_call.expires_at ??
                existingContent?.tool_call?.expires_at;
            if (auth != null) {
                newToolCall.auth = auth;
                newToolCall.expires_at = expiresAt;
            }
            if (finalUpdate) {
                newToolCall.progress = 1;
                newToolCall.output = contentPart.tool_call.output;
            }
            contentParts[index] = {
                type: _enum.ContentTypes.TOOL_CALL,
                tool_call: newToolCall,
            };
        }
        // Apply agentId (for MultiAgentGraph) and groupId (for parallel execution) to content parts
        // - agentId present → MultiAgentGraph (show agent labels)
        // - groupId present → parallel execution (render columns)
        const meta = contentMetaMap.get(index);
        if (meta?.agentId != null) {
            contentParts[index].agentId = meta.agentId;
        }
        if (meta?.groupId != null) {
            contentParts[index].groupId = meta.groupId;
        }
    };
    const aggregateContent = ({ event, data, }) => {
        if (event === _enum.GraphEvents.ON_SUMMARIZE_DELTA) {
            const deltaData = data;
            const runStep = stepMap.get(deltaData.id);
            if (!runStep) {
                console.warn('No run step found for summarize delta event');
                return;
            }
            updateContent(runStep.index, deltaData.delta.summary);
            return;
        }
        if (event === _enum.GraphEvents.ON_SUMMARIZE_COMPLETE) {
            const completeData = data;
            const summary = completeData.summary;
            if (!summary?.boundary) {
                return;
            }
            const runStep = stepMap.get(summary.boundary.messageId);
            if (!runStep) {
                return;
            }
            // Replace accumulated delta text with the authoritative final summary.
            // Multi-stage summarization streams deltas from each chunk, which
            // concatenate in updateContent.  This event carries only the correct
            // final text from the last stage.
            contentParts[runStep.index] = summary;
            return;
        }
        if (event === _enum.GraphEvents.ON_RUN_STEP) {
            const runStep = data;
            stepMap.set(runStep.id, runStep);
            // Track agentId (MultiAgentGraph) and groupId (parallel execution) separately
            // - agentId: present for all MultiAgentGraph runs (enables agent labels in UI)
            // - groupId: present only for parallel execution (enables column rendering)
            const hasAgentId = runStep.agentId != null && runStep.agentId !== '';
            const hasGroupId = runStep.groupId != null;
            if (hasAgentId || hasGroupId) {
                const existingMeta = contentMetaMap.get(runStep.index) ?? {};
                if (hasAgentId) {
                    existingMeta.agentId = runStep.agentId;
                }
                if (hasGroupId) {
                    existingMeta.groupId = runStep.groupId;
                }
                contentMetaMap.set(runStep.index, existingMeta);
            }
            if (runStep.summary != null) {
                updateContent(runStep.index, runStep.summary);
            }
            if (runStep.stepDetails.type === _enum.StepTypes.TOOL_CALLS &&
                runStep.stepDetails.tool_calls) {
                runStep.stepDetails.tool_calls.forEach((toolCall) => {
                    const toolCallId = toolCall.id ?? '';
                    if ('id' in toolCall && toolCallId) {
                        toolCallIdMap.set(runStep.id, toolCallId);
                    }
                    const contentPart = {
                        type: _enum.ContentTypes.TOOL_CALL,
                        tool_call: {
                            args: toolCall.args,
                            name: toolCall.name,
                            id: toolCallId,
                        },
                    };
                    updateContent(runStep.index, contentPart);
                });
            }
        }
        else if (event === _enum.GraphEvents.ON_MESSAGE_DELTA) {
            const messageDelta = data;
            const runStep = stepMap.get(messageDelta.id);
            if (!runStep) {
                console.warn('No run step or runId found for message delta event');
                return;
            }
            const deltaContent = Array.isArray(messageDelta.delta.content)
                ? messageDelta.delta.content
                : [messageDelta.delta.content];
            for (const contentPart of deltaContent) {
                if (contentPart != null) {
                    updateContent(runStep.index, contentPart);
                }
            }
        }
        else if (event === _enum.GraphEvents.ON_AGENT_UPDATE &&
            data?.agent_update) {
            const contentPart = data;
            if (!contentPart) {
                return;
            }
            updateContent(contentPart.agent_update.index, contentPart);
        }
        else if (event === _enum.GraphEvents.ON_REASONING_DELTA) {
            const reasoningDelta = data;
            const runStep = stepMap.get(reasoningDelta.id);
            if (!runStep) {
                console.warn('No run step or runId found for reasoning delta event');
                return;
            }
            const deltaContent = Array.isArray(reasoningDelta.delta.content)
                ? reasoningDelta.delta.content
                : [reasoningDelta.delta.content];
            for (const contentPart of deltaContent) {
                if (contentPart != null) {
                    updateContent(runStep.index, contentPart);
                }
            }
        }
        else if (event === _enum.GraphEvents.ON_RUN_STEP_DELTA) {
            const runStepDelta = data;
            const runStep = stepMap.get(runStepDelta.id);
            if (!runStep) {
                console.warn('No run step or runId found for run step delta event');
                return;
            }
            if (runStepDelta.delta.type === _enum.StepTypes.TOOL_CALLS &&
                runStepDelta.delta.tool_calls) {
                runStepDelta.delta.tool_calls.forEach((toolCallDelta) => {
                    const toolCallId = toolCallIdMap.get(runStepDelta.id);
                    const contentPart = {
                        type: _enum.ContentTypes.TOOL_CALL,
                        tool_call: {
                            args: toolCallDelta.args ?? '',
                            name: toolCallDelta.name,
                            id: toolCallId,
                            auth: runStepDelta.delta.auth,
                            expires_at: runStepDelta.delta.expires_at,
                        },
                    };
                    updateContent(runStep.index, contentPart);
                });
            }
        }
        else if (event === _enum.GraphEvents.ON_RUN_STEP_COMPLETED) {
            const { result } = data;
            const { id: stepId } = result;
            const runStep = stepMap.get(stepId);
            if (!runStep) {
                console.warn('No run step or runId found for completed step event');
                return;
            }
            if (result.type === _enum.ContentTypes.SUMMARY && 'summary' in result) {
                contentParts[runStep.index] = result.summary;
            }
            else if ('tool_call' in result) {
                const contentPart = {
                    type: _enum.ContentTypes.TOOL_CALL,
                    tool_call: result.tool_call,
                };
                updateContent(runStep.index, contentPart, true);
            }
        }
    };
    return { contentParts, aggregateContent, stepMap };
}

exports.ChatModelStreamHandler = ChatModelStreamHandler;
exports.createContentAggregator = createContentAggregator;
exports.extractKlaiSourcesFromText = extractKlaiSourcesFromText;
exports.getChunkContent = getChunkContent;
exports.getChunkSources = getChunkSources;
//# sourceMappingURL=stream.cjs.map
