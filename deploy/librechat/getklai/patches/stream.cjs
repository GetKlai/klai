const require_enum = require("./common/enum.cjs");
require("./common/index.cjs");
const require_ids = require("./messages/ids.cjs");
const require_truncation = require("./utils/truncation.cjs");
const require_events = require("./utils/events.cjs");
require("./messages/index.cjs");
const require_llm = require("./utils/llm.cjs");
const require_streamedToolCallSeals = require("./tools/streamedToolCallSeals.cjs");
const require_eagerEventExecution = require("./tools/eagerEventExecution.cjs");
const require_handlers = require("./tools/handlers.cjs");
const require_toolOutputReferences = require("./tools/toolOutputReferences.cjs");
//#region src/stream.ts
const LOCAL_CODING_BUNDLE_NAME_SET = new Set(require_enum.LOCAL_CODING_BUNDLE_NAMES);
/**
* Parses content to extract thinking sections enclosed in <think> tags using string operations
* @param content The content to parse
* @returns An object with separated text and thinking content
*/
function parseThinkingContent(content) {
	if (!content.includes("<think>")) return {
		text: content,
		thinking: ""
	};
	let textResult = "";
	const thinkingResult = [];
	let position = 0;
	while (position < content.length) {
		const thinkStart = content.indexOf("<think>", position);
		if (thinkStart === -1) {
			textResult += content.slice(position);
			break;
		}
		textResult += content.slice(position, thinkStart);
		const thinkEnd = content.indexOf("</think>", thinkStart);
		if (thinkEnd === -1) {
			textResult += content.slice(thinkStart);
			break;
		}
		const thinkContent = content.slice(thinkStart + 7, thinkEnd);
		thinkingResult.push(thinkContent);
		position = thinkEnd + 8;
	}
	return {
		text: textResult.trim(),
		thinking: thinkingResult.join("\n").trim()
	};
}
function getNonEmptyValue(possibleValues) {
	for (const value of possibleValues) if (value && value.trim() !== "") return value;
}
function isBatchSensitiveToolExecution(graph) {
	return graph.hookRegistry != null || graph.humanInTheLoop?.enabled === true;
}
function hasToolOutputReference(value) {
	if (typeof value === "string") return require_toolOutputReferences.TOOL_OUTPUT_REF_PATTERN.test(value);
	if (Array.isArray(value)) return value.some((item) => hasToolOutputReference(item));
	if (value !== null && typeof value === "object") return Object.values(value).some((item) => hasToolOutputReference(item));
	return false;
}
function isDirectGraphTool(name, agentContext) {
	if (name.startsWith("lc_transfer_to_")) return true;
	return (agentContext?.graphTools)?.some((tool) => "name" in tool && tool.name === name) === true;
}
function isDirectLocalTool(name, graph) {
	const toolExecution = graph.toolExecution;
	const engine = toolExecution?.engine;
	if (toolExecution == null || engine !== "local" && engine !== "cloudflare-sandbox") return false;
	if ((engine === "cloudflare-sandbox" ? toolExecution.cloudflare?.includeCodingTools : toolExecution.local?.includeCodingTools) === false) return require_enum.CODE_EXECUTION_TOOLS.has(name);
	return LOCAL_CODING_BUNDLE_NAME_SET.has(name);
}
function toCodeEnvFile(file, execSessionId) {
	const base = {
		id: file.id,
		resource_id: file.resource_id ?? file.id,
		name: file.name,
		storage_session_id: file.storage_session_id ?? execSessionId
	};
	const kind = file.kind ?? "user";
	if (kind === "skill" && file.version != null) return {
		...base,
		kind: "skill",
		version: file.version
	};
	if (kind === "agent") return {
		...base,
		kind: "agent"
	};
	return {
		...base,
		kind: "user"
	};
}
function getCodeSessionContext(graph, name) {
	if (!require_enum.CODE_EXECUTION_TOOLS.has(name) && name !== "skill" && name !== "read_file") return;
	const codeSession = graph.sessions.get("execute_code");
	if (codeSession?.session_id == null || codeSession.session_id === "") return;
	return {
		session_id: codeSession.session_id,
		files: codeSession.files?.map((file) => toCodeEnvFile(file, codeSession.session_id))
	};
}
function isEagerToolExecutionEnabledForBatch(args) {
	const { graph, metadata, agentContext } = args;
	if (graph.eagerEventToolExecution?.enabled !== true) return false;
	if ((agentContext?.toolDefinitions?.length ?? 0) === 0) return false;
	if (isBatchSensitiveToolExecution(graph)) return false;
	if (metadata?.["run_tools_with_code"] === true || metadata?.["run_tools_with_bash"] === true) return false;
	if (graph.handlerRegistry?.getHandler("on_tool_execute") == null && graph.eventToolExecutionAvailable !== true) return false;
	return true;
}
function hasFinalToolCallSignal(chunk) {
	const metadata = chunk.response_metadata;
	const finishReason = metadata?.finish_reason ?? metadata?.finishReason ?? metadata?.stop_reason ?? metadata?.stopReason;
	return finishReason === "tool_calls" || finishReason === "tool_use";
}
function canPrestartSequentialStreamedToolChunks(agentContext) {
	return agentContext?.provider === "anthropic";
}
function hasExplicitStreamedToolCallSeals(chunk) {
	return require_streamedToolCallSeals.getStreamedToolCallAdapter(chunk.response_metadata) != null;
}
/**
* True when a provider adapter marked every tool call on this chunk as
* complete on arrival (seal kind `all`), e.g. Google GenAI / Vertex AI, whose
* protocol delivers function calls as whole objects rather than arg deltas.
*/
function hasOnArrivalToolCallSeal(chunk) {
	const metadata = chunk.response_metadata;
	return require_streamedToolCallSeals.getStreamedToolCallAdapter(metadata) != null && require_streamedToolCallSeals.getStreamedToolCallSeal(metadata)?.kind === "all";
}
function hasDirectToolCallInBatch(args) {
	const { graph, agentContext, toolCalls } = args;
	return toolCalls.some((toolCall) => toolCall.name !== "" && (isDirectGraphTool(toolCall.name, agentContext) || isDirectLocalTool(toolCall.name, graph)));
}
function hasPotentialDirectToolInStreamContext(args) {
	const { graph, agentContext } = args;
	const engine = graph.toolExecution?.engine;
	if (engine === "local" || engine === "cloudflare-sandbox") return true;
	if ((agentContext?.graphTools?.length ?? 0) > 0) return true;
	return false;
}
function hasDirectToolCallChunkInBatch(args) {
	const { graph, agentContext, toolCallChunks } = args;
	return toolCallChunks?.some((toolCallChunk) => toolCallChunk.name != null && toolCallChunk.name !== "" && (isDirectGraphTool(toolCallChunk.name, agentContext) || isDirectLocalTool(toolCallChunk.name, graph))) === true;
}
function hasDirectToolCallChunkStateInStep(args) {
	const { graph, agentContext, stepKey } = args;
	const prefix = `${stepKey}\u0000`;
	for (const [key, state] of graph.eagerEventToolCallChunks) {
		if (!key.startsWith(prefix)) continue;
		const name = state.name;
		if (name != null && name !== "" && (isDirectGraphTool(name, agentContext) || isDirectLocalTool(name, graph))) return true;
	}
	return false;
}
function isGoogleServerSideToolContentPart(contentPart) {
	return contentPart.type === "toolCall" || contentPart.type === "toolResponse";
}
function isTextContentPart(contentPart) {
	return contentPart.type?.startsWith("text") ?? false;
}
function isReasoningContentPart(contentPart) {
	return (contentPart.type?.startsWith("thinking") ?? false) || (contentPart.type?.startsWith("reasoning") ?? false) || (contentPart.type?.startsWith("reasoning_content") ?? false) || contentPart.type === "redacted_thinking";
}
function getReasoningTextFromContentPart(contentPart) {
	return contentPart.thinking ?? contentPart.reasoning ?? contentPart.reasoningText?.text ?? "";
}
function getReasoningTextFromChunk(chunk, agentContext) {
	const reasoning = chunk.additional_kwargs?.[agentContext.reasoningKey];
	if (typeof reasoning === "string") return reasoning;
	return reasoning?.summary?.[0]?.text ?? "";
}
const googleServerSideToolStepIdsByGraph = /* @__PURE__ */ new WeakMap();
function markGoogleServerSideToolMessageStep(graph, stepId) {
	const stepIds = googleServerSideToolStepIdsByGraph.get(graph) ?? /* @__PURE__ */ new Set();
	stepIds.add(stepId);
	googleServerSideToolStepIdsByGraph.set(graph, stepIds);
}
function isGoogleServerSideToolMessageStep(graph, stepId) {
	return googleServerSideToolStepIdsByGraph.get(graph)?.has(stepId) === true;
}
function shouldStartFreshMessageStepAfterGoogleServerSideTool({ graph, stepId, runStep, content }) {
	if (runStep?.type !== "message_creation" || !isGoogleServerSideToolMessageStep(graph, stepId)) return false;
	if (typeof content === "string") return true;
	return content.every((c) => isTextContentPart(c)) || content.every((c) => isReasoningContentPart(c));
}
async function dispatchMessageCreationStep({ graph, stepKey, metadata }) {
	const messageId = require_ids.getMessageId(stepKey, graph, true) ?? "";
	return graph.dispatchRunStep(stepKey, {
		type: "message_creation",
		message_creation: { message_id: messageId }
	}, metadata);
}
async function dispatchMessageContentParts({ graph, stepKey, content, metadata }) {
	for (const contentPart of content) {
		const currentStepId = await dispatchMessageCreationStep({
			graph,
			stepKey,
			metadata
		});
		if (isGoogleServerSideToolContentPart(contentPart)) markGoogleServerSideToolMessageStep(graph, currentStepId);
		await graph.dispatchMessageDelta(currentStepId, { content: [contentPart] }, metadata);
	}
}
async function dispatchReasoningContentParts({ graph, stepKey, content, metadata }) {
	if (content.length === 0) return;
	const currentStepId = await dispatchMessageCreationStep({
		graph,
		stepKey,
		metadata
	});
	await graph.dispatchReasoningDelta(currentStepId, { content }, metadata);
}
async function dispatchGoogleServerSideToolStreamContent({ graph, stepKey, chunk, agentContext, content, metadata }) {
	const reasoningContent = [];
	const reasoningText = getReasoningTextFromChunk(chunk, agentContext);
	if (reasoningText !== "") reasoningContent.push({
		type: "think",
		think: reasoningText
	});
	reasoningContent.push(...content.filter((contentPart) => isReasoningContentPart(contentPart)).map((contentPart) => ({
		type: "think",
		think: getReasoningTextFromContentPart(contentPart)
	})).filter((contentPart) => contentPart.think !== ""));
	await dispatchReasoningContentParts({
		graph,
		stepKey,
		content: reasoningContent,
		metadata
	});
	await dispatchMessageContentParts({
		graph,
		stepKey,
		content: content.filter((contentPart) => isTextContentPart(contentPart) || isGoogleServerSideToolContentPart(contentPart)),
		metadata
	});
}
function createEagerToolExecutionPlan(args) {
	const { graph, metadata, agentContext, toolCalls, skipExisting = false } = args;
	if (!isEagerToolExecutionEnabledForBatch({
		graph,
		metadata,
		agentContext
	})) return;
	if (hasDirectToolCallInBatch({
		graph,
		agentContext,
		toolCalls
	})) return;
	if (graph.toolOutputReferences?.enabled === true && toolCalls.some((toolCall) => hasToolOutputReference(toolCall.args))) return;
	const candidateToolCalls = skipExisting ? toolCalls.filter((toolCall) => {
		if (toolCall.id == null || toolCall.id === "") return true;
		return !graph.eagerEventToolExecutions.has(toolCall.id);
	}) : toolCalls;
	if (candidateToolCalls.length === 0) return [];
	if (candidateToolCalls.some((toolCall) => toolCall.id == null || toolCall.id === "" || toolCall.name === "" || !skipExisting && graph.eagerEventToolExecutions.has(toolCall.id))) return;
	const plan = require_eagerEventExecution.buildToolExecutionRequestPlan({
		toolCalls: candidateToolCalls.map((toolCall) => ({
			id: toolCall.id,
			name: toolCall.name,
			args: toolCall.args,
			stepId: graph.toolCallStepIds.get(toolCall.id) ?? "",
			codeSessionContext: getCodeSessionContext(graph, toolCall.name)
		})),
		usageCount: graph.getEagerEventToolUsageCount(agentContext?.agentId)
	});
	if (plan == null) return;
	return plan.requests.map((request) => ({
		id: request.id,
		toolName: request.name,
		coercedArgs: request.args,
		request
	}));
}
function startEagerToolExecutions(args) {
	const { graph, metadata, agentContext, toolCalls, skipExisting } = args;
	const entries = createEagerToolExecutionPlan({
		graph,
		metadata,
		agentContext,
		toolCalls,
		skipExisting
	});
	if (entries == null || entries.length === 0) return;
	const records = [];
	const promise = new Promise((resolve, reject) => {
		let dispatchSettled = false;
		let resultSettled = false;
		let settledResults;
		const maybeResolve = () => {
			if (dispatchSettled && resultSettled) resolve(settledResults ?? []);
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
			reject
		};
		require_events.safeDispatchCustomEvent("on_tool_execute", batchRequest, graph.config).then(() => {
			dispatchSettled = true;
			maybeResolve();
		}).catch(reject);
	}).then(async (results) => {
		await dispatchEagerToolCompletions({
			graph,
			agentContext,
			records,
			results
		});
		return { results };
	}, (error) => ({ error: require_eagerEventExecution.normalizeError(error) }));
	for (const entry of entries) {
		const record = {
			toolCallId: entry.id,
			toolName: entry.toolName,
			args: entry.coercedArgs,
			request: entry.request,
			promise
		};
		records.push(record);
		graph.eagerEventToolExecutions.set(entry.id, record);
	}
}
async function dispatchEagerToolCompletions(args) {
	const { graph, agentContext, records, results } = args;
	const recordById = new Map(records.map((record) => [record.toolCallId, record]));
	const maxToolResultChars = agentContext?.maxToolResultChars ?? require_truncation.calculateMaxToolResultChars(agentContext?.maxContextTokens);
	for (const result of results) {
		const record = recordById.get(result.toolCallId);
		if (record == null) continue;
		if (graph.eagerEventToolExecutions.get(result.toolCallId) !== record) continue;
		const stepId = record.request.stepId ?? graph.toolCallStepIds.get(result.toolCallId) ?? "";
		if (stepId === "") continue;
		const output = result.status === "error" ? `Error: ${result.errorMessage ?? "Unknown error"}\n Please fix your mistakes.` : require_truncation.truncateToolResultContent(typeof result.content === "string" ? result.content : JSON.stringify(result.content), maxToolResultChars);
		try {
			if (await require_events.safeDispatchCustomEvent("on_run_step_completed", { result: {
				id: stepId,
				index: record.request.turn ?? 0,
				type: "tool_call",
				eager: true,
				tool_call: {
					args: JSON.stringify(record.request.args),
					name: record.toolName,
					id: result.toolCallId,
					output,
					progress: 1
				}
			} }, graph.config) === false) continue;
			record.completionDispatched = true;
		} catch (error) {
			console.warn(`[stream] eager completion dispatch failed for toolCallId=${result.toolCallId}:`, error instanceof Error ? error.message : error);
		}
	}
}
function getEagerToolChunkKey(stepKey, toolCallChunk) {
	let chunkKey;
	if (typeof toolCallChunk.index === "number") chunkKey = String(toolCallChunk.index);
	else if (toolCallChunk.id != null && toolCallChunk.id !== "") chunkKey = toolCallChunk.id;
	if (chunkKey == null) return;
	return `${stepKey}\u0000${chunkKey}`;
}
function getEagerToolChunkIndex(toolCallChunk) {
	return typeof toolCallChunk.index === "number" ? toolCallChunk.index : void 0;
}
function pruneEagerToolCallChunkStates(args) {
	const { graph, stepKey, toolCallIds, clearStep = false } = args;
	const prefix = `${stepKey}\u0000`;
	for (const [key, state] of graph.eagerEventToolCallChunks) {
		if (!key.startsWith(prefix)) continue;
		if (clearStep || state.id != null && toolCallIds?.has(state.id) === true) graph.eagerEventToolCallChunks.delete(key);
	}
}
function isEagerToolChunkStateComplete(state) {
	return state.id != null && state.id !== "" && state.name != null && state.name !== "" && require_eagerEventExecution.coerceRecordArgs(state.argsText) != null;
}
function mergeToolCallArgsText(existing, incoming) {
	if (incoming === "") return existing;
	if (existing === "") return incoming;
	if (incoming === existing) try {
		JSON.parse(incoming);
		return incoming;
	} catch {
		return `${existing}${incoming}`;
	}
	if (incoming.startsWith(existing)) return incoming;
	if (existing.startsWith(incoming)) return existing;
	try {
		JSON.parse(existing);
		JSON.parse(incoming);
		return incoming;
	} catch {}
	for (let overlap = Math.min(existing.length, incoming.length); overlap >= 8; overlap -= 1) if (existing.endsWith(incoming.slice(0, overlap))) return `${existing}${incoming.slice(overlap)}`;
	return `${existing}${incoming}`;
}
function recordEagerToolCallChunks(args) {
	const { graph, stepKey, toolCallChunks } = args;
	if (toolCallChunks == null || toolCallChunks.length === 0) return;
	for (const toolCallChunk of toolCallChunks) {
		const key = getEagerToolChunkKey(stepKey, toolCallChunk);
		if (key == null) continue;
		const incomingId = toolCallChunk.id != null && toolCallChunk.id !== "" ? toolCallChunk.id : void 0;
		const incomingName = toolCallChunk.name != null && toolCallChunk.name !== "" ? toolCallChunk.name : void 0;
		const previous = graph.eagerEventToolCallChunks.get(key);
		const shouldReset = previous != null && (incomingId != null && previous.id != null && incomingId !== previous.id || incomingName != null && previous.name != null && incomingName !== previous.name);
		const existing = previous == null || shouldReset ? { argsText: "" } : previous;
		const id = incomingId ?? existing.id;
		const name = incomingName ?? existing.name;
		const incomingArgs = toolCallChunk.args ?? "";
		const next = {
			id,
			name,
			argsText: incomingArgs !== "" && incomingArgs.length > 1 && incomingArgs === existing.lastArgsFragment ? existing.argsText : mergeToolCallArgsText(existing.argsText, incomingArgs),
			index: getEagerToolChunkIndex(toolCallChunk) ?? existing.index,
			lastArgsFragment: incomingArgs !== "" ? incomingArgs : existing.lastArgsFragment
		};
		graph.eagerEventToolCallChunks.set(key, next);
	}
}
function getStreamedReadyToolCalls(args) {
	const { graph, stepKey, toolCallChunks, seal, allowSequentialSeal = false, sealAll = false } = args;
	const currentIndices = /* @__PURE__ */ new Set();
	for (const toolCallChunk of toolCallChunks ?? []) {
		const index = getEagerToolChunkIndex(toolCallChunk);
		if (index != null) currentIndices.add(index);
	}
	const highestCurrentIndex = currentIndices.size > 0 ? Math.max(...currentIndices) : void 0;
	const prefix = `${stepKey}\u0000`;
	const readyEntries = [];
	for (const [key, state] of graph.eagerEventToolCallChunks) {
		if (!key.startsWith(prefix)) continue;
		if (state.id != null && graph.eagerEventToolExecutions.has(state.id)) {
			graph.eagerEventToolCallChunks.delete(key);
			continue;
		}
		if (!isEagerToolChunkStateComplete(state)) continue;
		const isSealedByLaterChunk = allowSequentialSeal && highestCurrentIndex != null && state.index != null && state.index < highestCurrentIndex && !currentIndices.has(state.index);
		const isSealedExplicitly = seal?.kind === "single" && (seal.id != null && state.id === seal.id || seal.index != null && state.index === seal.index);
		if (sealAll || seal?.kind === "all" || isSealedByLaterChunk || isSealedExplicitly) readyEntries.push({
			key,
			state
		});
	}
	pruneEagerToolCallChunkStates({
		graph,
		stepKey,
		toolCallIds: new Set(readyEntries.map(({ state }) => state.id).filter((id) => id != null && id !== ""))
	});
	if (sealAll) pruneEagerToolCallChunkStates({
		graph,
		stepKey,
		clearStep: true
	});
	return readyEntries.sort((left, right) => (left.state.index ?? 0) - (right.state.index ?? 0)).flatMap(({ state }) => {
		const args = require_eagerEventExecution.coerceRecordArgs(state.argsText);
		if (args == null) return [];
		return [{
			id: state.id,
			name: state.name ?? "",
			args
		}];
	});
}
function startReadyStreamedEagerToolExecutions(args) {
	const { graph, metadata, agentContext, stepKey, toolCallChunks, seal, allowSequentialSeal, sealAll } = args;
	if (hasPotentialDirectToolInStreamContext({
		graph,
		agentContext
	}) || hasDirectToolCallChunkInBatch({
		graph,
		agentContext,
		toolCallChunks
	}) || hasDirectToolCallChunkStateInStep({
		graph,
		agentContext,
		stepKey
	}) || !isEagerToolExecutionEnabledForBatch({
		graph,
		metadata,
		agentContext
	})) return;
	const toolCalls = getStreamedReadyToolCalls({
		graph,
		stepKey,
		toolCallChunks,
		seal,
		allowSequentialSeal,
		sealAll
	});
	if (toolCalls.length === 0) return;
	startEagerToolExecutions({
		graph,
		metadata,
		agentContext,
		toolCalls,
		skipExisting: true
	});
}
function getChunkSources(chunk) {
	const candidates = [
		chunk?.sources,
		chunk?.additional_kwargs?.sources,
		chunk?.response_metadata?.sources,
		chunk?.additional_kwargs?.delta?.sources
	];
	for (const candidate of candidates) {
		if (Array.isArray(candidate) && candidate.length > 0) return candidate;
	}
	return void 0;
}
const KLAI_SOURCES_MARKER_RE = /<!--\s*klai_sources=([A-Za-z0-9_-]+={0,2})\s*-->/g;
function decodeKlaiSourcesMarker(encoded) {
	try {
		const padded = encoded + "=".repeat((4 - encoded.length % 4) % 4);
		const json = Buffer.from(padded.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
		const parsed = JSON.parse(json);
		return Array.isArray(parsed) ? parsed : void 0;
	} catch (_error) {
		console.warn("[klai-patch] stream.decodeKlaiSourcesMarker decode failed");
		return void 0;
	}
}
function extractKlaiSourcesFromText(text) {
	let sources;
	const cleanText = text.replace(KLAI_SOURCES_MARKER_RE, (_match, encoded) => {
		const decoded = decodeKlaiSourcesMarker(encoded);
		if (decoded && decoded.length > 0) sources = decoded;
		return "";
	});
	return {
		text: cleanText,
		sources
	};
}
function getChunkContent({ chunk, provider, reasoningKey }) {
	if (require_llm.isGoogleLike(provider) && Array.isArray(chunk?.content) && chunk.content.some((c) => isGoogleServerSideToolContentPart(c))) return chunk.content;
	if ((provider === "openAI" || provider === "azureOpenAI") && (chunk?.additional_kwargs?.reasoning)?.summary?.[0]?.text != null && ((chunk?.additional_kwargs?.reasoning)?.summary?.[0]?.text?.length ?? 0) > 0) return (chunk?.additional_kwargs?.reasoning)?.summary?.[0]?.text;
	if (provider === "openrouter") {
		if (typeof chunk?.content === "string" && chunk.content !== "") return chunk.content;
		const reasoning = chunk?.additional_kwargs?.reasoning;
		if (reasoning != null && reasoning !== "") return reasoning;
		const reasoningContent = chunk?.additional_kwargs?.reasoning_content;
		if (reasoningContent != null && reasoningContent !== "") return reasoningContent;
		return chunk?.content;
	}
	const keyedReasoning = chunk?.additional_kwargs?.[reasoningKey];
	if (typeof chunk?.content === "string" && chunk.content !== "" && keyedReasoning != null && keyedReasoning !== "") return chunk.content;
	return (keyedReasoning ?? "") || chunk?.content;
}
function isDisableStreamingEnabled(clientOptions) {
	return clientOptions != null && "disableStreaming" in clientOptions && clientOptions.disableStreaming === true;
}
function hasReasoningContent(value) {
	if (typeof value === "string") return value !== "";
	if (Array.isArray(value)) return value.length > 0;
	if (value == null) return false;
	return value.summary?.some((summary) => summary.text != null && summary.text.length > 0) === true;
}
function shouldDeferMixedFinalReasoningChunk({ chunk, agentContext }) {
	if ((chunk.tool_calls?.length ?? 0) > 0 || (chunk.tool_call_chunks?.length ?? 0) > 0 || typeof chunk.content !== "string" || chunk.content === "") return false;
	const additionalKwargs = chunk.additional_kwargs;
	if (agentContext.provider === "openrouter" && hasReasoningContent(additionalKwargs?.reasoning_details)) return true;
	if (!isDisableStreamingEnabled(agentContext.clientOptions)) return false;
	return hasReasoningContent(additionalKwargs?.[agentContext.reasoningKey]) || hasReasoningContent(additionalKwargs?.reasoning_content) || hasReasoningContent(additionalKwargs?.reasoning) || hasReasoningContent(additionalKwargs?.reasoning_details);
}
function hasCurrentTextDeltaStep({ graph, metadata }) {
	if (metadata == null) return false;
	const baseStepKey = graph.getStepBaseKey(metadata);
	for (const [stepKey, stepIds] of graph.stepKeyIds) {
		if (stepKey !== baseStepKey && !stepKey.startsWith(`${baseStepKey}_`)) continue;
		if (stepIds.some((stepId) => graph.messageStepHasTextDeltas.has(stepId))) return true;
	}
	return false;
}
function shouldSkipLateOpenRouterReasoningChunk({ chunk, agentContext, graph, metadata }) {
	if (agentContext.provider !== "openrouter" || (chunk.tool_calls?.length ?? 0) > 0 || (chunk.tool_call_chunks?.length ?? 0) > 0 || chunk.content != null && chunk.content !== "") return false;
	return (hasReasoningContent(chunk.additional_kwargs?.reasoning) || hasReasoningContent(chunk.additional_kwargs?.reasoning_content) || hasReasoningContent(chunk.additional_kwargs?.reasoning_details)) && hasCurrentTextDeltaStep({
		graph,
		metadata
	});
}
var ChatModelStreamHandler = class {
	async handle(event, data, metadata, graph) {
		if (!graph) throw new Error("Graph not found");
		if (!graph.config) throw new Error("Config not found in graph");
		if (!data.chunk) {
			console.warn(`No chunk found in ${event} event`);
			return;
		}
		const agentContext = graph.getAgentContext(metadata);
		const chunk = data.chunk;
		const content = getChunkContent({
			chunk,
			reasoningKey: agentContext.reasoningKey,
			provider: agentContext.provider
		});
		if (await require_handlers.handleServerToolResult({
			graph,
			content,
			metadata,
			agentContext
		})) return;
		if (shouldDeferMixedFinalReasoningChunk({
			chunk,
			agentContext
		})) return;
		if (shouldSkipLateOpenRouterReasoningChunk({
			chunk,
			agentContext,
			graph,
			metadata
		})) return;
		this.handleReasoning(chunk, agentContext);
		const stepKey = graph.getStepKey(metadata);
		let hasToolCalls = false;
		const hasToolCallChunks = (chunk.tool_call_chunks && chunk.tool_call_chunks.length > 0) ?? false;
		const hasGoogleServerSideToolContent = require_llm.isGoogleLike(agentContext.provider) && Array.isArray(content) && content.some((c) => isGoogleServerSideToolContentPart(c));
		if (hasGoogleServerSideToolContent && Array.isArray(content)) await dispatchGoogleServerSideToolStreamContent({
			graph,
			stepKey,
			chunk,
			agentContext,
			content,
			metadata
		});
		if (chunk.tool_calls && chunk.tool_calls.length > 0 && chunk.tool_calls.every((tc) => tc.id != null && tc.id !== "" && tc.name != null && tc.name !== "")) {
			hasToolCalls = true;
			await require_handlers.handleToolCalls(chunk.tool_calls, metadata, graph);
			if (hasFinalToolCallSignal(chunk)) {
				startEagerToolExecutions({
					graph,
					metadata,
					agentContext,
					toolCalls: chunk.tool_calls,
					skipExisting: true
				});
				if (!hasToolCallChunks) pruneEagerToolCallChunkStates({
					graph,
					stepKey,
					clearStep: true
				});
			} else if (hasOnArrivalToolCallSeal(chunk) && !hasPotentialDirectToolInStreamContext({
				graph,
				agentContext
			})) startEagerToolExecutions({
				graph,
				metadata,
				agentContext,
				toolCalls: chunk.tool_calls,
				skipExisting: true
			});
		}
		const isEmptyContent = typeof content === "undefined" || !content.length || typeof content === "string" && !content;
		/** Set a preliminary message ID if found in empty chunk */
		const isEmptyChunk = isEmptyContent && !hasToolCallChunks;
		if (isEmptyChunk && (chunk.id ?? "") !== "" && !graph.prelimMessageIdsByStepKey.has(chunk.id ?? "")) graph.prelimMessageIdsByStepKey.set(stepKey, chunk.id ?? "");
		else if (isEmptyChunk) return;
		if (hasToolCallChunks && chunk.tool_call_chunks && chunk.tool_call_chunks.length && typeof chunk.tool_call_chunks[0]?.index === "number") {
			const streamedToolCallSeal = require_streamedToolCallSeals.getStreamedToolCallSeal(chunk.response_metadata);
			const allowSequentialSeal = canPrestartSequentialStreamedToolChunks(agentContext) || require_streamedToolCallSeals.streamedToolCallAdapterAllowsSequentialSeal(chunk.response_metadata);
			const canStreamEager = (allowSequentialSeal || hasExplicitStreamedToolCallSeals(chunk)) && !hasPotentialDirectToolInStreamContext({
				graph,
				agentContext
			}) && isEagerToolExecutionEnabledForBatch({
				graph,
				metadata,
				agentContext
			});
			if (canStreamEager) recordEagerToolCallChunks({
				graph,
				stepKey,
				toolCallChunks: chunk.tool_call_chunks
			});
			await require_handlers.handleToolCallChunks({
				graph,
				stepKey,
				toolCallChunks: chunk.tool_call_chunks,
				metadata
			});
			if (canStreamEager) startReadyStreamedEagerToolExecutions({
				graph,
				metadata,
				agentContext,
				stepKey,
				toolCallChunks: chunk.tool_call_chunks,
				seal: streamedToolCallSeal,
				allowSequentialSeal,
				sealAll: hasFinalToolCallSignal(chunk)
			});
		}
		if (isEmptyContent) return;
		if (hasGoogleServerSideToolContent) return;
		const message_id = require_ids.getMessageId(stepKey, graph) ?? "";
		if (message_id) await graph.dispatchRunStep(stepKey, {
			type: "message_creation",
			message_creation: { message_id }
		}, metadata);
		let stepId = graph.getStepIdByKey(stepKey);
		let runStep = graph.getRunStep(stepId);
		if (shouldStartFreshMessageStepAfterGoogleServerSideTool({
			graph,
			stepId,
			runStep,
			content
		})) {
			stepId = await dispatchMessageCreationStep({
				graph,
				stepKey,
				metadata
			});
			runStep = graph.getRunStep(stepId);
		}
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
		if (typeof content === "string" && runStep.type === "tool_calls") return;
		else if (hasToolCallChunks && (chunk.tool_call_chunks?.some((tc) => tc.args === content) ?? false)) return;
		else if (typeof content === "string") {
			const extracted = extractKlaiSourcesFromText(content);
			const textContent = extracted.text;
			const sources = getChunkSources(chunk) ?? extracted.sources;
			if (agentContext.currentTokenType === "text") await graph.dispatchMessageDelta(stepId, { content: [{
				type: "text",
				text: textContent,
				...sources ? { sources } : {}
			}] }, metadata);
			else if (agentContext.currentTokenType === "think_and_text") {
				const { text, thinking } = parseThinkingContent(textContent);
				if (thinking) await graph.dispatchReasoningDelta(stepId, { content: [{
					type: "think",
					think: thinking
				}] }, metadata);
				if (text) {
					agentContext.currentTokenType = "text";
					agentContext.tokenTypeSwitch = "content";
					const newStepKey = graph.getStepKey(metadata);
					const message_id = require_ids.getMessageId(newStepKey, graph) ?? "";
					await graph.dispatchRunStep(newStepKey, {
						type: "message_creation",
						message_creation: { message_id }
					}, metadata);
					const newStepId = graph.getStepIdByKey(newStepKey);
					await graph.dispatchMessageDelta(newStepId, { content: [{
						type: "text",
						text,
						...sources ? { sources } : {}
					}] }, metadata);
				}
			} else await graph.dispatchReasoningDelta(stepId, { content: [{
				type: "think",
				think: content
			}] }, metadata);
		}
		else if (content.every((c) => isTextContentPart(c))) await graph.dispatchMessageDelta(stepId, { content }, metadata);
		else if (content.every((c) => isReasoningContentPart(c))) await graph.dispatchReasoningDelta(stepId, { content: content.map((c) => ({
			type: "think",
			think: c.thinking ?? c.reasoning ?? c.reasoningText?.text ?? ""
		})) }, metadata);
	}
	handleReasoning(chunk, agentContext) {
		let reasoning_content = chunk.additional_kwargs?.[agentContext.reasoningKey];
		if (Array.isArray(chunk.content) && (chunk.content[0]?.type === "thinking" || chunk.content[0]?.type === "reasoning" || chunk.content[0]?.type === "reasoning_content" || chunk.content[0]?.type === "redacted_thinking")) reasoning_content = "valid";
		else if ((agentContext.provider === "openAI" || agentContext.provider === "azureOpenAI") && reasoning_content != null && typeof reasoning_content !== "string" && reasoning_content.summary?.[0]?.text != null && reasoning_content.summary[0].text) reasoning_content = "valid";
		else if (agentContext.provider === "openrouter" && (chunk.content == null || chunk.content === "") && (chunk.additional_kwargs?.reasoning_details != null && Array.isArray(chunk.additional_kwargs.reasoning_details) && chunk.additional_kwargs.reasoning_details.length > 0 || typeof chunk.additional_kwargs?.reasoning === "string" && chunk.additional_kwargs.reasoning !== "" || typeof chunk.additional_kwargs?.reasoning_content === "string" && chunk.additional_kwargs.reasoning_content !== "")) reasoning_content = "valid";
		if (reasoning_content != null && reasoning_content !== "" && (chunk.content == null || chunk.content === "" || reasoning_content === "valid")) {
			agentContext.currentTokenType = "think";
			agentContext.tokenTypeSwitch = "reasoning";
			return;
		} else if (agentContext.tokenTypeSwitch === "reasoning" && agentContext.currentTokenType !== "text" && (chunk.content != null && chunk.content !== "" || (chunk.tool_calls?.length ?? 0) > 0 || (chunk.tool_call_chunks?.length ?? 0) > 0)) {
			agentContext.currentTokenType = "text";
			agentContext.tokenTypeSwitch = "content";
			agentContext.reasoningTransitionCount++;
		} else if (chunk.content != null && typeof chunk.content === "string" && chunk.content.includes("<think>") && chunk.content.includes("</think>")) {
			agentContext.currentTokenType = "think_and_text";
			agentContext.tokenTypeSwitch = "content";
		} else if (chunk.content != null && typeof chunk.content === "string" && chunk.content.includes("<think>")) {
			agentContext.currentTokenType = "think";
			agentContext.tokenTypeSwitch = "content";
		} else if (agentContext.lastToken != null && agentContext.lastToken.includes("</think>")) {
			agentContext.currentTokenType = "text";
			agentContext.tokenTypeSwitch = "content";
		}
		if (typeof chunk.content !== "string") return;
		agentContext.lastToken = chunk.content;
	}
};
function createContentAggregator() {
	const contentParts = [];
	const stepMap = /* @__PURE__ */ new Map();
	const toolCallIdMap = /* @__PURE__ */ new Map();
	const contentMetaMap = /* @__PURE__ */ new Map();
	const getFirstContentPart = (content) => {
		if (content == null) return;
		return Array.isArray(content) ? content[0] : content;
	};
	const updateContent = (index, contentPart, finalUpdate = false) => {
		if (!contentPart) {
			console.warn("No content part found in 'updateContent'");
			return;
		}
		const partType = contentPart.type ?? "";
		if (!partType) {
			console.warn("No content type found in content part");
			return;
		}
		if (!contentParts[index] && partType !== "tool_call") contentParts[index] = { type: partType };
		if (!partType.startsWith(contentParts[index]?.type ?? "")) {
			console.warn("Content type mismatch");
			return;
		}
		if (partType.startsWith("text") && "text" in contentPart && typeof contentPart.text === "string") {
			const currentContent = contentParts[index];
			const extracted = extractKlaiSourcesFromText(contentPart.text);
			const update = {
				type: "text",
				text: (currentContent.text || "") + extracted.text
			};
			if (contentPart.tool_call_ids) update.tool_call_ids = contentPart.tool_call_ids;
			const incomingSources = contentPart.sources ?? extracted.sources;
			if (Array.isArray(incomingSources) && incomingSources.length > 0) update.sources = incomingSources;
			else if (Array.isArray(currentContent.sources) && currentContent.sources.length > 0) update.sources = currentContent.sources;
			contentParts[index] = update;
		} else if (partType.startsWith("think") && "think" in contentPart && typeof contentPart.think === "string") contentParts[index] = {
			type: "think",
			think: (contentParts[index].think || "") + contentPart.think
		};
		else if (partType.startsWith("agent_update") && "agent_update" in contentPart && contentPart.agent_update != null) contentParts[index] = {
			type: "agent_update",
			agent_update: contentPart.agent_update
		};
		else if (partType === "toolCall" || partType === "toolResponse") contentParts[index] = contentPart;
		else if (partType === "summary") {
			const currentSummary = contentParts[index];
			const incoming = contentPart;
			contentParts[index] = {
				...incoming,
				content: [...currentSummary?.content ?? [], ...incoming.content ?? []]
			};
		} else if (partType === "image_url" && "image_url" in contentPart) contentParts[index] = { ...contentParts[index] };
		else if (partType === "tool_call" && "tool_call" in contentPart) {
			const incomingName = contentPart.tool_call.name;
			const incomingId = contentPart.tool_call.id;
			const toolCallArgs = contentPart.tool_call.args;
			if (!(incomingName != null && incomingName !== "") && !finalUpdate) return;
			const existingContent = contentParts[index];
			if (!finalUpdate && existingContent?.tool_call?.progress === 1) return;
			/** When args are a valid object, they are likely already invoked */
			let args = finalUpdate || typeof existingContent?.tool_call?.args === "object" || typeof toolCallArgs === "object" ? contentPart.tool_call.args : (existingContent?.tool_call?.args ?? "") + (toolCallArgs ?? "");
			if (finalUpdate && args == null && existingContent?.tool_call?.args != null) args = existingContent.tool_call.args;
			const newToolCall = {
				id: getNonEmptyValue([incomingId, existingContent?.tool_call?.id]) ?? "",
				name: getNonEmptyValue([incomingName, existingContent?.tool_call?.name]) ?? "",
				args,
				type: "tool_call"
			};
			const auth = contentPart.tool_call.auth ?? existingContent?.tool_call?.auth;
			const expiresAt = contentPart.tool_call.expires_at ?? existingContent?.tool_call?.expires_at;
			if (auth != null) {
				newToolCall.auth = auth;
				newToolCall.expires_at = expiresAt;
			}
			if (finalUpdate) {
				newToolCall.progress = 1;
				newToolCall.output = contentPart.tool_call.output;
			}
			contentParts[index] = {
				type: "tool_call",
				tool_call: newToolCall
			};
		}
		const meta = contentMetaMap.get(index);
		if (meta?.agentId != null) contentParts[index].agentId = meta.agentId;
		if (meta?.groupId != null) contentParts[index].groupId = meta.groupId;
	};
	const aggregateContent = ({ event, data }) => {
		if (event === "on_summarize_delta") {
			const deltaData = data;
			const runStep = stepMap.get(deltaData.id);
			if (!runStep) {
				console.warn("No run step found for summarize delta event");
				return;
			}
			updateContent(runStep.index, deltaData.delta.summary);
			return;
		}
		if (event === "on_summarize_complete") {
			const summary = data.summary;
			if (!summary?.boundary) return;
			const runStep = stepMap.get(summary.boundary.messageId);
			if (!runStep) return;
			contentParts[runStep.index] = summary;
			return;
		}
		if (event === "on_run_step") {
			const runStep = data;
			stepMap.set(runStep.id, runStep);
			const hasAgentId = runStep.agentId != null && runStep.agentId !== "";
			const hasGroupId = runStep.groupId != null;
			if (hasAgentId || hasGroupId) {
				const existingMeta = contentMetaMap.get(runStep.index) ?? {};
				if (hasAgentId) existingMeta.agentId = runStep.agentId;
				if (hasGroupId) existingMeta.groupId = runStep.groupId;
				contentMetaMap.set(runStep.index, existingMeta);
			}
			if (runStep.summary != null) updateContent(runStep.index, runStep.summary);
			if (runStep.stepDetails.type === "tool_calls" && runStep.stepDetails.tool_calls) runStep.stepDetails.tool_calls.forEach((toolCall) => {
				const toolCallId = toolCall.id ?? "";
				if ("id" in toolCall && toolCallId) toolCallIdMap.set(runStep.id, toolCallId);
				const contentPart = {
					type: "tool_call",
					tool_call: {
						args: toolCall.args,
						name: toolCall.name,
						id: toolCallId
					}
				};
				updateContent(runStep.index, contentPart);
			});
		} else if (event === "on_message_delta") {
			const messageDelta = data;
			const runStep = stepMap.get(messageDelta.id);
			if (!runStep) {
				console.warn("No run step or runId found for message delta event");
				return;
			}
			const messageDeltaContent = Array.isArray(messageDelta.delta.content) ? messageDelta.delta.content : [messageDelta.delta.content];
			if (messageDeltaContent.length > 1) console.warn("[klai-patch] stream.delta-array guard saw multi-part delta");
			for (const contentPart of messageDeltaContent) {
				if (contentPart != null) updateContent(runStep.index, contentPart);
			}
		} else if (event === "on_agent_update" && data?.agent_update) {
			const contentPart = data;
			if (!contentPart) return;
			updateContent(contentPart.agent_update.index, contentPart);
		} else if (event === "on_reasoning_delta") {
			const reasoningDelta = data;
			const runStep = stepMap.get(reasoningDelta.id);
			if (!runStep) {
				console.warn("No run step or runId found for reasoning delta event");
				return;
			}
			const reasoningDeltaContent = Array.isArray(reasoningDelta.delta.content) ? reasoningDelta.delta.content : [reasoningDelta.delta.content];
			if (reasoningDeltaContent.length > 1) console.warn("[klai-patch] stream.delta-array guard saw multi-part delta");
			for (const contentPart of reasoningDeltaContent) {
				if (contentPart != null) updateContent(runStep.index, contentPart);
			}
		} else if (event === "on_run_step_delta") {
			const runStepDelta = data;
			const runStep = stepMap.get(runStepDelta.id);
			if (!runStep) {
				console.warn("No run step or runId found for run step delta event");
				return;
			}
			if (runStepDelta.delta.type === "tool_calls" && runStepDelta.delta.tool_calls) runStepDelta.delta.tool_calls.forEach((toolCallDelta) => {
				const toolCallId = toolCallIdMap.get(runStepDelta.id);
				const contentPart = {
					type: "tool_call",
					tool_call: {
						args: toolCallDelta.args ?? "",
						name: toolCallDelta.name,
						id: toolCallId,
						auth: runStepDelta.delta.auth,
						expires_at: runStepDelta.delta.expires_at
					}
				};
				updateContent(runStep.index, contentPart);
			});
		} else if (event === "on_run_step_completed") {
			const { result } = data;
			const { id: stepId } = result;
			const runStep = stepMap.get(stepId);
			if (!runStep) {
				console.warn("No run step or runId found for completed step event");
				return;
			}
			if (result.type === "summary" && "summary" in result) contentParts[runStep.index] = result.summary;
			else if ("tool_call" in result) {
				const contentPart = {
					type: "tool_call",
					tool_call: result.tool_call
				};
				updateContent(runStep.index, contentPart, true);
			}
		}
	};
	return {
		contentParts,
		aggregateContent,
		stepMap
	};
}
//#endregion
exports.ChatModelStreamHandler = ChatModelStreamHandler;
exports.createContentAggregator = createContentAggregator;
exports.extractKlaiSourcesFromText = extractKlaiSourcesFromText;
exports.getChunkContent = getChunkContent;
exports.getChunkSources = getChunkSources;

//# sourceMappingURL=stream.cjs.map