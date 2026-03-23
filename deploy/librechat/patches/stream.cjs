'use strict';

var _enum = require('./common/enum.cjs');
var handlers = require('./tools/handlers.cjs');
require('./messages/core.cjs');
var ids = require('./messages/ids.cjs');
require('@langchain/core/messages');

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
        let hasToolCalls = false;
        if (chunk.tool_calls &&
            chunk.tool_calls.length > 0 &&
            chunk.tool_calls.every((tc) => tc.id != null &&
                tc.id !== '' &&
                tc.name != null &&
                tc.name !== '')) {
            hasToolCalls = true;
            await handlers.handleToolCalls(chunk.tool_calls, metadata, graph);
        }
        const hasToolCallChunks = (chunk.tool_call_chunks && chunk.tool_call_chunks.length > 0) ?? false;
        const isEmptyContent = typeof content === 'undefined' ||
            !content.length ||
            (typeof content === 'string' && !content);
        /** Set a preliminary message ID if found in empty chunk */
        const isEmptyChunk = isEmptyContent && !hasToolCallChunks;
        if (isEmptyChunk &&
            (chunk.id ?? '') !== '' &&
            !graph.prelimMessageIdsByStepKey.has(chunk.id ?? '')) {
            const stepKey = graph.getStepKey(metadata);
            graph.prelimMessageIdsByStepKey.set(stepKey, chunk.id ?? '');
        }
        else if (isEmptyChunk) {
            return;
        }
        const stepKey = graph.getStepKey(metadata);
        if (hasToolCallChunks &&
            chunk.tool_call_chunks &&
            chunk.tool_call_chunks.length &&
            typeof chunk.tool_call_chunks[0]?.index === 'number') {
            await handlers.handleToolCallChunks({
                graph,
                stepKey,
                toolCallChunks: chunk.tool_call_chunks,
                metadata,
            });
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
            if (agentContext.currentTokenType === _enum.ContentTypes.TEXT) {
                await graph.dispatchMessageDelta(stepId, {
                    content: [
                        {
                            type: _enum.ContentTypes.TEXT,
                            text: content,
                        },
                    ],
                });
            }
            else if (agentContext.currentTokenType === 'think_and_text') {
                const { text, thinking } = parseThinkingContent(content);
                if (thinking) {
                    await graph.dispatchReasoningDelta(stepId, {
                        content: [
                            {
                                type: _enum.ContentTypes.THINK,
                                think: thinking,
                            },
                        ],
                    });
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
                            },
                        ],
                    });
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
                });
            }
        }
        else if (content.every((c) => c.type?.startsWith(_enum.ContentTypes.TEXT) ?? false)) {
            await graph.dispatchMessageDelta(stepId, {
                content,
            });
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
                        c.reasoningText?.text ??
                        '',
                })),
            });
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
            const update = {
                type: _enum.ContentTypes.TEXT,
                text: (currentContent.text || '') + contentPart.text,
            };
            if (contentPart.tool_call_ids) {
                update.tool_call_ids = contentPart.tool_call_ids;
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
            // Initialize content slot for MESSAGE_CREATION steps.
            // Without this, Mistral (content: null + tool_calls) leaves contentParts[index]
            // as a sparse hole, which MongoDB serializes as null and crashes formatAgentMessages.
            if (runStep.stepDetails.type === _enum.StepTypes.MESSAGE_CREATION &&
                runStep.index != null) {
                contentParts[runStep.index] = { type: 'text', text: '' };
            }
            // Store tool call IDs if present
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
            if (messageDelta.delta.content) {
                const contentPart = Array.isArray(messageDelta.delta.content)
                    ? messageDelta.delta.content[0]
                    : messageDelta.delta.content;
                updateContent(runStep.index, contentPart);
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
            if (reasoningDelta.delta.content) {
                const contentPart = Array.isArray(reasoningDelta.delta.content)
                    ? reasoningDelta.delta.content[0]
                    : reasoningDelta.delta.content;
                updateContent(runStep.index, contentPart);
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
                console.warn('No run step or runId found for completed tool call event');
                return;
            }
            const contentPart = {
                type: _enum.ContentTypes.TOOL_CALL,
                tool_call: result.tool_call,
            };
            updateContent(runStep.index, contentPart, true);
        }
    };
    return { contentParts, aggregateContent, stepMap };
}

exports.ChatModelStreamHandler = ChatModelStreamHandler;
exports.createContentAggregator = createContentAggregator;
exports.getChunkContent = getChunkContent;
//# sourceMappingURL=stream.cjs.map
