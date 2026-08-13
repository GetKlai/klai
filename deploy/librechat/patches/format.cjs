require("../common/enum.cjs");
require("../common/index.cjs");
const require_langchain = require("./langchain.cjs");
const require_message_inputs = require("../llm/anthropic/utils/message_inputs.cjs");
const require_events = require("../utils/events.cjs");
let _langchain_core_messages = require("@langchain/core/messages");
//#region src/messages/format.ts
/**
* Formats a message with media content (images, documents, videos, audios) to API payload format.
*
* @param params - The parameters for formatting.
* @returns - The formatted message.
*/
const formatMediaMessage = ({ message, endpoint, mediaParts }) => {
	const result = {
		...message,
		content: []
	};
	if (endpoint === "anthropic") {
		result.content = [...mediaParts, {
			type: "text",
			text: message.content
		}];
		return result;
	}
	result.content = [{
		type: "text",
		text: message.content
	}, ...mediaParts];
	return result;
};
const isRecord = (value) => value != null && typeof value === "object";
const _klaiWarned = /* @__PURE__ */ new Set();
const warnOnce = (message) => {
	if (_klaiWarned.has(message)) return;
	_klaiWarned.add(message);
	console.warn(`[klai-patch] ${message}`);
};
function withMessageRole(message, role) {
	const roleMessage = message;
	if (roleMessage.role === role) return roleMessage;
	Object.defineProperty(roleMessage, "role", {
		value: role,
		writable: true,
		enumerable: false,
		configurable: true
	});
	return roleMessage;
}
/**
* Formats a message to OpenAI payload format based on the provided options.
*
* @param params - The parameters for formatting.
* @returns - The formatted message.
*/
const formatMessage = ({ message, userName, endpoint, assistantName, langChain = false }) => {
	if (!isRecord(message)) {
		console.warn("[klai-patch] format.formatMessage guard replaced non-object message with {}");
		message = {};
	}
	let { role: _role, _name, sender, text, content: _content, lc_id } = message;
	if (lc_id && lc_id[2] && !langChain) _role = {
		SystemMessage: "system",
		HumanMessage: "user",
		AIMessage: "assistant"
	}[lc_id[2]] || _role;
	const role = _role ?? (sender != null && sender && sender.toLowerCase() === "user" ? "user" : "assistant");
	const formattedMessage = {
		role,
		content: _content ?? text ?? ""
	};
	if (_name != null && _name) formattedMessage.name = _name;
	if (userName != null && userName && formattedMessage.role === "user") formattedMessage.name = userName;
	if (assistantName != null && assistantName && formattedMessage.role === "assistant") formattedMessage.name = assistantName;
	if (formattedMessage.name != null && formattedMessage.name) {
		formattedMessage.name = formattedMessage.name.replace(/[^a-zA-Z0-9_-]/g, "_");
		if (formattedMessage.name.length > 64) formattedMessage.name = formattedMessage.name.substring(0, 64);
	}
	const { image_urls, documents, videos, audios } = message;
	const mediaParts = [];
	if (Array.isArray(documents) && documents.length > 0) mediaParts.push(...documents);
	if (Array.isArray(videos) && videos.length > 0) mediaParts.push(...videos);
	if (Array.isArray(audios) && audios.length > 0) mediaParts.push(...audios);
	if (Array.isArray(image_urls) && image_urls.length > 0) mediaParts.push(...image_urls);
	if (mediaParts.length > 0 && role === "user") {
		const mediaMessage = formatMediaMessage({
			message: {
				...formattedMessage,
				content: typeof formattedMessage.content === "string" ? formattedMessage.content : ""
			},
			mediaParts,
			endpoint
		});
		if (!langChain) return mediaMessage;
		return withMessageRole(new _langchain_core_messages.HumanMessage(require_langchain.toLangChainMessageFields(mediaMessage)), "user");
	}
	if (!langChain) return formattedMessage;
	if (role === "user") return withMessageRole(new _langchain_core_messages.HumanMessage(require_langchain.toLangChainMessageFields(formattedMessage)), "user");
	else if (role === "assistant") return withMessageRole(new _langchain_core_messages.AIMessage(require_langchain.toLangChainMessageFields(formattedMessage)), "assistant");
	else return withMessageRole(new _langchain_core_messages.SystemMessage(require_langchain.toLangChainMessageFields(formattedMessage)), "system");
};
/**
* Formats an array of messages for LangChain.
*
* @param messages - The array of messages to format.
* @param formatOptions - The options for formatting each message.
* @returns - The array of formatted LangChain messages.
*/
const formatLangChainMessages = (messages, formatOptions) => {
	return messages.filter((msg) => {
		if (isRecord(msg)) return true;
		console.warn("[klai-patch] format.formatLangChainMessages guard dropped non-object message entry");
		return false;
	}).map((msg) => {
		return formatMessage({
			...formatOptions,
			message: msg,
			langChain: true
		});
	});
};
/**
* Formats a LangChain message object by merging properties from `lc_kwargs` or `kwargs` and `additional_kwargs`.
*
* @param message - The message object to format.
* @returns - The formatted LangChain message.
*/
const formatFromLangChain = (message) => {
	const { additional_kwargs = {}, ...message_kwargs } = message.lc_kwargs ?? message.kwargs ?? {};
	return {
		...message_kwargs,
		...additional_kwargs
	};
};
function extractReasoningContent(part) {
	if (part == null || typeof part !== "object") return "";
	if (part.type === "think") {
		const think = part.think;
		return typeof think === "string" ? think : "";
	}
	if (part.type === "thinking") {
		const thinking = part.thinking;
		return typeof thinking === "string" ? thinking : "";
	}
	if (part.type === "reasoning") {
		const reasoning = part.reasoning;
		return typeof reasoning === "string" ? reasoning : "";
	}
	if (part.type === "reasoning_content") {
		const reasoningText = part.reasoningText;
		return typeof reasoningText.text === "string" ? reasoningText.text : "";
	}
	return "";
}
function parseServerToolInput(args) {
	if (typeof args === "string") try {
		const parsed = JSON.parse(args);
		return parsed != null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
	} catch {
		return {};
	}
	return args != null && typeof args === "object" ? args : {};
}
function getTextContent(part) {
	const { text } = part;
	return typeof text === "string" ? text : "";
}
function hasMeaningfulAssistantContent(part) {
	if (part.type === "text") return getTextContent(part).trim().length > 0;
	if (part.type === "tool_call" || part.type === "error" || part.type === "agent_update" || part.type === "summary") return false;
	if (part.type === "think" || part.type === "thinking" || part.type === "reasoning" || part.type === "reasoning_content" || part.type === "redacted_thinking") return extractReasoningContent(part).trim().length > 0;
	return part.type != null && part.type !== "";
}
function getToolUseId(part) {
	if (!("tool_use_id" in part) || typeof part.tool_use_id !== "string") return;
	return part.tool_use_id;
}
function isValidServerToolResult(part) {
	if (getToolUseId(part)?.startsWith("srvtoolu_") !== true || !("content" in part)) return false;
	const { content } = part;
	return Array.isArray(content) || content != null && typeof content === "object" && "type" in content && content.type === "web_search_tool_result_error";
}
function getToolCallId(part) {
	if (part.type !== "tool_call") return;
	const id = part.tool_call?.id;
	return typeof id === "string" && id !== "" ? id : void 0;
}
function hasToolCallOutput(part) {
	if (part.type !== "tool_call") return false;
	const output = part.tool_call?.output;
	return output != null && output !== "";
}
/**
* Helper function to format an assistant message
* @param message The message to format
* @param options Optional formatting options
* @returns Array of formatted messages
*/
function formatAssistantMessage(message, options) {
	const formattedMessages = [];
	let currentContent = [];
	let lastAIMessage = null;
	let hasReasoning = false;
	let pendingReasoningContent = "";
	const emittedServerToolUseIds = /* @__PURE__ */ new Set();
	const pendingServerToolUses = /* @__PURE__ */ new Map();
	const shouldPreserveReasoningContent = options?.preserveReasoningContent === true;
	const serverToolResultIds = /* @__PURE__ */ new Set();
	const preferredToolCallParts = /* @__PURE__ */ new Map();
	const takePendingReasoningContent = () => {
		if (!shouldPreserveReasoningContent || !pendingReasoningContent) return;
		const reasoningContent = pendingReasoningContent;
		pendingReasoningContent = "";
		return reasoningContent;
	};
	const createAIMessage = (content) => {
		const reasoningContent = takePendingReasoningContent();
		return withMessageRole(new _langchain_core_messages.AIMessage({
			content,
			...reasoningContent != null && { additional_kwargs: { reasoning_content: reasoningContent } }
		}), "assistant");
	};
	const attachPendingReasoningContent = (aiMessage) => {
		const reasoningContent = takePendingReasoningContent();
		if (reasoningContent == null) return;
		aiMessage.additional_kwargs.reasoning_content = typeof aiMessage.additional_kwargs.reasoning_content === "string" ? `${aiMessage.additional_kwargs.reasoning_content}${reasoningContent}` : reasoningContent;
	};
	const flushPendingServerToolUse = (toolUseId) => {
		for (const [id, content] of pendingServerToolUses) {
			pendingServerToolUses.delete(id);
			if (id === toolUseId) {
				currentContent.push(content);
				emittedServerToolUseIds.add(id);
				return;
			}
		}
	};
	if (Array.isArray(message.content)) {
		const contentParts = message.content;
		for (const part of contentParts) {
			if (part == null) continue;
			if (isValidServerToolResult(part)) serverToolResultIds.add(getToolUseId(part) ?? "");
			if (options?.provider === "anthropic") {
				const toolCallId = getToolCallId(part);
				if (toolCallId == null) continue;
				const preferredPart = preferredToolCallParts.get(toolCallId);
				if (preferredPart == null || !hasToolCallOutput(preferredPart) && hasToolCallOutput(part)) preferredToolCallParts.set(toolCallId, part);
			}
		}
		for (const part of contentParts) {
			if (part == null) continue;
			const toolUseId = getToolUseId(part);
			if (toolUseId != null) {
				const isServerToolResult = isValidServerToolResult(part);
				if (toolUseId.startsWith("srvtoolu_") && !isServerToolResult) continue;
				flushPendingServerToolUse(toolUseId);
				if (isServerToolResult) {
					currentContent.push(part);
					continue;
				}
			} else if (hasMeaningfulAssistantContent(part)) {
				for (const id of pendingServerToolUses.keys()) if (!serverToolResultIds.has(id)) pendingServerToolUses.delete(id);
			}
			if (part.type === "text" && part.tool_call_ids) {
				if (currentContent.length > 0) {
					if (currentContent.some((content) => content.type !== "text")) {
						currentContent.push(part);
						lastAIMessage = createAIMessage(require_langchain.toLangChainContent(currentContent));
						formattedMessages.push(lastAIMessage);
						currentContent = [];
						continue;
					}
					let content = currentContent.reduce((acc, curr) => {
						if (curr.type === "text") return `${acc}${getTextContent(curr)}\n`;
						return acc;
					}, "");
					content = `${content}\n${getTextContent(part)}`.trim();
					lastAIMessage = createAIMessage(content);
					formattedMessages.push(lastAIMessage);
					currentContent = [];
					continue;
				}
				lastAIMessage = createAIMessage(getTextContent(part));
				formattedMessages.push(lastAIMessage);
			} else if (part.type === "tool_call") {
				if (part.tool_call == null) continue;
				const toolCallId = getToolCallId(part);
				if (options?.provider === "anthropic" && toolCallId != null && preferredToolCallParts.get(toolCallId) !== part) continue;
				const { output, args: _args, ..._tool_call } = part.tool_call;
				if (_tool_call.name == null || _tool_call.name === "" && (output == null || output === "")) continue;
				if (options?.provider === "anthropic" && typeof _tool_call.id === "string" && _tool_call.id.startsWith("srvtoolu_")) {
					if (!serverToolResultIds.has(_tool_call.id) && options.preserveUnpairedServerToolUses !== true) continue;
					if (emittedServerToolUseIds.has(_tool_call.id) || pendingServerToolUses.has(_tool_call.id)) continue;
					pendingServerToolUses.set(_tool_call.id, {
						type: "server_tool_use",
						id: _tool_call.id,
						name: _tool_call.name,
						input: parseServerToolInput(_args)
					});
					continue;
				}
				if (!lastAIMessage) {
					lastAIMessage = createAIMessage("");
					formattedMessages.push(lastAIMessage);
				} else attachPendingReasoningContent(lastAIMessage);
				const tool_call = _tool_call;
				let args = _args;
				try {
					if (typeof _args === "string") args = JSON.parse(_args);
				} catch {
					if (typeof _args === "string") args = { input: _args };
				}
				tool_call.args = args;
				if (options?.provider === "anthropic" && Array.isArray(lastAIMessage.content)) {
					const content = lastAIMessage.content;
					content.push({
						type: "tool_use",
						id: require_message_inputs.normalizeAnthropicToolCallId(tool_call.id ?? ""),
						name: tool_call.name,
						input: args
					});
					lastAIMessage.content = content;
				} else {
					if (!lastAIMessage.tool_calls) lastAIMessage.tool_calls = [];
					lastAIMessage.tool_calls.push(tool_call);
				}
				formattedMessages.push(withMessageRole(new _langchain_core_messages.ToolMessage({
					tool_call_id: tool_call.id ?? "",
					name: tool_call.name,
					content: output != null ? output : ""
				}), "tool"));
			} else if (part.type === "think" || part.type === "thinking" || part.type === "reasoning" || part.type === "reasoning_content" || part.type === "redacted_thinking") {
				hasReasoning = true;
				pendingReasoningContent += extractReasoningContent(part);
				continue;
			} else if (part.type === "error" || part.type === "agent_update" || part.type === "summary") continue;
			else {
				if (part.type === "text" && !getTextContent(part).trim()) continue;
				currentContent.push(part);
			}
		}
		for (const content of pendingServerToolUses.values()) currentContent.push(content);
	}
	if (hasReasoning && currentContent.length > 0) {
		let content = "";
		for (const part of currentContent) {
			if (part.type !== "text") {
				formattedMessages.push(createAIMessage(require_langchain.toLangChainContent(currentContent)));
				return formattedMessages;
			}
			content += `${getTextContent(part)}\n`;
		}
		content = content.trim();
		if (content) formattedMessages.push(createAIMessage(content));
	} else if (currentContent.length > 0) formattedMessages.push(createAIMessage(require_langchain.toLangChainContent(currentContent)));
	return formattedMessages;
}
function getSourceMessageId(message) {
	const candidate = message.messageId ?? message.id;
	if (typeof candidate !== "string") return;
	const normalized = candidate.trim();
	return normalized.length > 0 ? normalized : void 0;
}
/**
* Labels all agent content for parallel patterns (fan-out/fan-in)
* Groups consecutive content by agent and wraps with clear labels
*/
function labelAllAgentContent(contentParts, agentIdMap, agentNames) {
	const result = [];
	let currentAgentId;
	let agentContentBuffer = [];
	const flushAgentBuffer = () => {
		if (agentContentBuffer.length === 0) return;
		if (currentAgentId != null && currentAgentId !== "") {
			const agentName = (agentNames?.[currentAgentId] ?? "") || currentAgentId;
			const formattedParts = [];
			formattedParts.push(`--- ${agentName} ---`);
			for (const part of agentContentBuffer) if (part.type === "think") {
				const thinkContent = part.think || "";
				if (thinkContent) formattedParts.push(`${agentName}: ${JSON.stringify({
					type: "think",
					think: thinkContent
				})}`);
			} else if (part.type === "text") {
				const textContent = part.text ?? "";
				if (textContent) formattedParts.push(`${agentName}: ${textContent}`);
			} else if (part.type === "tool_call") formattedParts.push(`${agentName}: ${JSON.stringify({
				type: "tool_call",
				tool_call: part.tool_call
			})}`);
			formattedParts.push(`--- End of ${agentName} ---`);
			result.push({
				type: "text",
				text: formattedParts.join("\n\n")
			});
		} else result.push(...agentContentBuffer);
		agentContentBuffer = [];
	};
	for (let i = 0; i < contentParts.length; i++) {
		const part = contentParts[i];
		const agentId = agentIdMap[i];
		if (agentId !== currentAgentId && currentAgentId !== void 0) flushAgentBuffer();
		currentAgentId = agentId;
		agentContentBuffer.push(part);
	}
	flushAgentBuffer();
	return result;
}
/**
* Groups content parts by agent and formats them with agent labels
* This preprocesses multi-agent content to prevent identity confusion
*
* @param contentParts - The content parts from a run
* @param agentIdMap - Map of content part index to agent ID
* @param agentNames - Optional map of agent ID to display name
* @param options - Configuration options
* @param options.labelNonTransferContent - If true, labels all agent transitions (for parallel patterns)
* @returns Modified content parts with agent labels where appropriate
*/
const labelContentByAgent = (contentParts, agentIdMap, agentNames, options) => {
	if (!agentIdMap || Object.keys(agentIdMap).length === 0) return contentParts;
	if (options?.labelNonTransferContent === true) return labelAllAgentContent(contentParts, agentIdMap, agentNames);
	const result = [];
	let currentAgentId;
	let agentContentBuffer = [];
	let transferToolCallIndex;
	let transferToolCallId;
	const flushAgentBuffer = () => {
		if (agentContentBuffer.length === 0) return;
		if (currentAgentId != null && currentAgentId !== "" && transferToolCallIndex !== void 0) {
			const agentName = (agentNames?.[currentAgentId] ?? "") || currentAgentId;
			const formattedParts = [];
			formattedParts.push(`--- Transfer to ${agentName} ---`);
			for (const part of agentContentBuffer) if (part.type === "think") formattedParts.push(`${agentName}: ${JSON.stringify({
				type: "think",
				think: part.think
			})}`);
			else if ("text" in part && part.type === "text") {
				const textContent = part.text ?? "";
				if (textContent) formattedParts.push(`${agentName}: ${JSON.stringify({
					type: "text",
					text: textContent
				})}`);
			} else if (part.type === "tool_call") formattedParts.push(`${agentName}: ${JSON.stringify({
				type: "tool_call",
				tool_call: part.tool_call
			})}`);
			formattedParts.push(`--- End of ${agentName} response ---`);
			if (transferToolCallIndex < result.length) {
				const transferToolCall = result[transferToolCallIndex];
				if (transferToolCall.type === "tool_call" && transferToolCall.tool_call?.id === transferToolCallId) transferToolCall.tool_call.output = formattedParts.join("\n\n");
			}
		} else result.push(...agentContentBuffer);
		agentContentBuffer = [];
		transferToolCallIndex = void 0;
		transferToolCallId = void 0;
	};
	for (let i = 0; i < contentParts.length; i++) {
		const part = contentParts[i];
		const agentId = agentIdMap[i];
		const isTransferTool = (part.type === "tool_call" && part.tool_call?.name?.startsWith("lc_transfer_to_")) ?? false;
		if (agentId !== currentAgentId && currentAgentId !== void 0) flushAgentBuffer();
		currentAgentId = agentId;
		if (isTransferTool) {
			flushAgentBuffer();
			result.push(part);
			transferToolCallIndex = result.length - 1;
			transferToolCallId = part.tool_call?.id;
			currentAgentId = void 0;
		} else agentContentBuffer.push(part);
	}
	flushAgentBuffer();
	return result;
};
/** Extracts tool names from a tool_search output JSON string. */
function extractToolNamesFromSearchOutput(output) {
	try {
		const parsed = JSON.parse(output);
		if (typeof parsed === "object" && parsed !== null && Array.isArray(parsed.tools)) return parsed.tools.map((t) => t.name).filter((name) => typeof name === "string");
	} catch {
		/** Output may have warnings prepended, try to find JSON within it */
		const jsonMatch = output.match(/\{[\s\S]*\}/);
		if (jsonMatch) try {
			const parsed = JSON.parse(jsonMatch[0]);
			if (typeof parsed === "object" && parsed !== null && Array.isArray(parsed.tools)) return parsed.tools.map((t) => t.name).filter((name) => typeof name === "string");
		} catch {}
	}
	return [];
}
function getLatestSummaryBoundary(payload) {
	let summaryBoundary;
	for (let i = 0; i < payload.length; i++) {
		const message = payload[i];
		if (!isRecord(message)) {
			console.warn("[klai-patch] format.getLatestSummaryBoundary guard skipped non-object message entry");
			continue;
		}
		if (!Array.isArray(message.content)) continue;
		for (let j = 0; j < message.content.length; j++) {
			const part = message.content[j];
			if (part == null || part.type !== "summary") continue;
			const summaryPart = part;
			let summaryText = (summaryPart.content ?? []).map((block) => "text" in block ? block.text : "").join("").trim();
			if (summaryText.length === 0 && typeof summaryPart.text === "string") summaryText = summaryPart.text.trim();
			if (summaryText.length === 0) continue;
			summaryBoundary = {
				messageIndex: i,
				contentIndex: j,
				text: summaryText,
				tokenCount: typeof summaryPart.tokenCount === "number" && Number.isFinite(summaryPart.tokenCount) ? summaryPart.tokenCount : 0
			};
		}
	}
	return summaryBoundary;
}
function applySummaryBoundary(message, messageIndex, summaryBoundary) {
	if (!summaryBoundary) return message;
	if (messageIndex < summaryBoundary.messageIndex) return null;
	if (messageIndex !== summaryBoundary.messageIndex || !isRecord(message) || !Array.isArray(message.content)) {
		if (messageIndex === summaryBoundary.messageIndex && !isRecord(message)) {
			console.warn("[klai-patch] format.applySummaryBoundary guard passed through non-object message");
		}
		return message;
	}
	return {
		...message,
		content: message.content.slice(summaryBoundary.contentIndex + 1)
	};
}
function contentPartCharLength(part) {
	const record = part;
	let len = 0;
	if (typeof record.text === "string") len += record.text.length;
	if (typeof record.thinking === "string") len += record.thinking.length;
	const { input } = record;
	if (typeof input === "string") len += input.length;
	else if (input != null && typeof input === "object") len += JSON.stringify(input).length;
	return len;
}
/** Extracts the skillName from a skill tool_call's args (string or object). */
function extractSkillName(args) {
	let parsed;
	if (typeof args === "string") try {
		parsed = JSON.parse(args);
	} catch {}
	else parsed = args;
	const name = parsed?.skillName;
	return typeof name === "string" && name !== "" ? name : void 0;
}
/**
* Formats an array of messages for LangChain, handling tool calls and creating ToolMessage instances.
*
* @param payload - The array of messages to format.
* @param indexTokenCountMap - Optional map of message indices to token counts.
* @param tools - Optional set of tool names that are allowed in the request.
* @param skills - Optional map of skill name to body for reconstructing skill HumanMessages.
* @param options - Optional formatting options (provider, skipSkillBodyNames).
* @returns - Object containing formatted messages and updated indexTokenCountMap if provided.
*/
const formatAgentMessages = (payload, indexTokenCountMap, tools, skills, options) => {
	const messages = [];
	const updatedIndexTokenCountMap = {};
	let boundaryTokenAdjustment;
	const indexMapping = {};
	const summaryBoundary = getLatestSummaryBoundary(payload);
	/**
	* Create a mutable copy of the tools set that can be expanded dynamically.
	* When we encounter tool_search results, we add discovered tools to this set,
	* making their subsequent tool calls valid.
	*/
	const discoveredTools = tools ? new Set(tools) : void 0;
	for (let i = 0; i < payload.length; i++) {
		const rawMessage = payload[i];
		if (!isRecord(rawMessage)) {
			console.warn("[klai-patch] format.formatAgentMessages guard dropped non-object payload entry");
			indexMapping[i] = [];
			continue;
		}
		const sourceMessageId = getSourceMessageId(rawMessage);
		let message = applySummaryBoundary(rawMessage, i, summaryBoundary);
		if (!message) {
			indexMapping[i] = [];
			continue;
		}
		if (typeof message.content === "string") message = {
			...message,
			content: [{
				type: "text",
				["text"]: message.content
			}]
		};
		else if (Array.isArray(message.content) && message.content.length === 0) {
			indexMapping[i] = [];
			continue;
		}
		if (message.role !== "assistant") {
			const formattedMessage = formatMessage({
				message,
				langChain: true
			});
			if (sourceMessageId != null && sourceMessageId !== "") formattedMessage.id = sourceMessageId;
			messages.push(formattedMessage);
			indexMapping[i] = [messages.length - 1];
			continue;
		}
		const startMessageIndex = messages.length;
		/**
		* If tools set is provided, process tool_calls:
		* - Keep valid tool_calls (tools in the set or dynamically discovered)
		* - Convert invalid tool_calls to string representation for context preservation
		* - Dynamically expand the set when tool_search results are encountered
		*/
		let processedMessage = message;
		let pendingSkillNames;
		if (discoveredTools) {
			const content = message.content;
			if (content != null && Array.isArray(content)) {
				const filteredContent = [];
				const invalidToolCallIds = /* @__PURE__ */ new Set();
				const invalidToolStrings = [];
				for (const part of content) {
					if (!isRecord(part)) {
						console.warn("[klai-patch] format.formatAgentMessages guard dropped non-object content part");
						continue;
					}
					if (part.type !== "tool_call") {
						filteredContent.push(part);
						continue;
					}
					/** Skip malformed tool_call entries */
					if (part.tool_call == null || part.tool_call.name == null || part.tool_call.name === "") {
						if (typeof part.tool_call?.id === "string" && part.tool_call.id !== "") invalidToolCallIds.add(part.tool_call.id);
						continue;
					}
					const toolName = part.tool_call.name;
					/**
					* If this is a tool_search result with output, extract discovered tool names
					* and add them to the discoveredTools set for subsequent validation.
					*/
					if (toolName === "tool_search" && typeof part.tool_call.output === "string" && part.tool_call.output !== "") {
						const extracted = extractToolNamesFromSearchOutput(part.tool_call.output);
						for (const name of extracted) discoveredTools.add(name);
					}
					if (discoveredTools.has(toolName)) {
						filteredContent.push(part);
						if (toolName === "skill" && skills?.size != null && skills.size > 0) {
							const skillName = extractSkillName(part.tool_call.args) ?? "";
							if (skillName) (pendingSkillNames ??= /* @__PURE__ */ new Set()).add(skillName);
						}
					} else {
						/** Invalid tool - convert to string for context preservation */
						if (typeof part.tool_call.id === "string" && part.tool_call.id !== "") invalidToolCallIds.add(part.tool_call.id);
						const output = part.tool_call.output ?? "";
						invalidToolStrings.push(`Tool: ${toolName}, ${output}`);
					}
				}
				/** Remove tool_call_ids references to invalid tools from text parts */
				if (invalidToolCallIds.size > 0) {
					for (const part of filteredContent) if (part.type === "text" && Array.isArray(part.tool_call_ids)) {
						part.tool_call_ids = part.tool_call_ids.filter((id) => !invalidToolCallIds.has(id));
						if (part.tool_call_ids.length === 0) delete part.tool_call_ids;
					}
				}
				/** Append invalid tool strings to the content for context preservation */
				if (invalidToolStrings.length > 0) {
					/** Find the last text part or create one */
					let lastTextPartIndex = -1;
					for (let j = filteredContent.length - 1; j >= 0; j--) if (filteredContent[j].type === "text") {
						lastTextPartIndex = j;
						break;
					}
					const invalidToolText = invalidToolStrings.join("\n");
					if (lastTextPartIndex >= 0) {
						const lastTextPart = filteredContent[lastTextPartIndex];
						const existingText = lastTextPart["text"] ?? lastTextPart.text ?? "";
						filteredContent[lastTextPartIndex] = {
							...lastTextPart,
							["text"]: existingText ? `${existingText}\n${invalidToolText}` : invalidToolText
						};
					} else
 /** No text part exists, create one */
					filteredContent.push({
						type: "text",
						["text"]: invalidToolText
					});
				}
				/** Use filtered content if we made any changes */
				if (filteredContent.length !== content.length || invalidToolStrings.length > 0) processedMessage = {
					...message,
					content: filteredContent
				};
			}
		}
		/** When tools filtering is off, still detect skill tool_calls for body reconstruction */
		if (!discoveredTools && skills?.size != null && skills.size > 0) {
			const content = processedMessage.content;
			if (Array.isArray(content)) for (const part of content) {
				if (part.type !== "tool_call" || part.tool_call?.name !== "skill") continue;
				const skillName = extractSkillName(part.tool_call.args) ?? "";
				if (skillName) (pendingSkillNames ??= /* @__PURE__ */ new Set()).add(skillName);
			}
		}
		const formattedMessages = formatAssistantMessage(processedMessage, {
			preserveUnpairedServerToolUses: i === payload.length - 1,
			preserveReasoningContent: options?.preserveReasoningContent ?? options?.provider === "deepseek",
			provider: options?.provider
		});
		if (sourceMessageId != null && sourceMessageId !== "") for (const formattedMessage of formattedMessages) formattedMessage.id = sourceMessageId;
		messages.push(...formattedMessages);
		const endMessageIndex = messages.length;
		if (pendingSkillNames?.size != null && pendingSkillNames.size > 0) {
			const skipSkillBodyNames = options?.skipSkillBodyNames;
			for (const skillName of pendingSkillNames) {
				if (skipSkillBodyNames != null && skipSkillBodyNames.has(skillName)) continue;
				const body = skills?.get(skillName) ?? "";
				if (body) messages.push(withMessageRole(new _langchain_core_messages.HumanMessage({
					content: body,
					additional_kwargs: {
						role: "user",
						isMeta: true,
						source: "skill",
						skillName
					}
				}), "user"));
			}
		}
		const resultIndices = [];
		for (let j = startMessageIndex; j < endMessageIndex; j++) resultIndices.push(j);
		indexMapping[i] = resultIndices;
	}
	if (indexTokenCountMap) for (let originalIndex = 0; originalIndex < payload.length; originalIndex++) {
		const resultIndices = indexMapping[originalIndex] || [];
		let tokenCount = indexTokenCountMap[originalIndex];
		if (tokenCount === void 0) continue;
		if (summaryBoundary && originalIndex === summaryBoundary.messageIndex && Array.isArray(payload[originalIndex].content)) {
			const content = payload[originalIndex].content;
			const { contentIndex } = summaryBoundary;
			if (contentIndex >= 0 && contentIndex < content.length - 1) {
				let totalCharLen = 0;
				let remainingCharLen = 0;
				for (let p = 0; p < content.length; p++) {
					const charLen = contentPartCharLength(content[p]);
					totalCharLen += charLen;
					if (p > contentIndex) remainingCharLen += charLen;
				}
				if (totalCharLen > 0) {
					const original = tokenCount;
					tokenCount = Math.max(1, Math.round(tokenCount * (remainingCharLen / totalCharLen)));
					boundaryTokenAdjustment = {
						original,
						adjusted: tokenCount,
						remainingChars: remainingCharLen,
						totalChars: totalCharLen
					};
				}
			}
		}
		const msgCount = resultIndices.length;
		if (msgCount === 1) {
			updatedIndexTokenCountMap[resultIndices[0]] = tokenCount;
			continue;
		}
		if (msgCount < 2) continue;
		let totalLength = 0;
		const lastIdx = msgCount - 1;
		const lengths = new Array(msgCount);
		for (let k = 0; k < msgCount; k++) {
			const msg = messages[resultIndices[k]];
			const { content } = msg;
			let len = 0;
			if (typeof content === "string") len = content.length;
			else if (Array.isArray(content)) {
				for (const part of content) if (typeof part === "string") len += part.length;
				else if (part != null && typeof part === "object") {
					const val = part.text ?? part.content;
					if (typeof val === "string") len += val.length;
				}
			}
			const toolCalls = msg.tool_calls;
			if (Array.isArray(toolCalls)) for (const tc of toolCalls) {
				if (typeof tc.name === "string") len += tc.name.length;
				const { args } = tc;
				if (typeof args === "string") len += args.length;
				else if (args != null) len += JSON.stringify(args).length;
			}
			lengths[k] = len;
			totalLength += len;
		}
		if (totalLength === 0) {
			const countPerMessage = Math.floor(tokenCount / msgCount);
			for (let k = 0; k < lastIdx; k++) updatedIndexTokenCountMap[resultIndices[k]] = countPerMessage;
			updatedIndexTokenCountMap[resultIndices[lastIdx]] = tokenCount - countPerMessage * lastIdx;
		} else {
			let distributed = 0;
			for (let k = 0; k < lastIdx; k++) {
				const share = Math.floor(lengths[k] / totalLength * tokenCount);
				updatedIndexTokenCountMap[resultIndices[k]] = share;
				distributed += share;
			}
			updatedIndexTokenCountMap[resultIndices[lastIdx]] = tokenCount - distributed;
		}
	}
	return {
		messages,
		indexTokenCountMap: indexTokenCountMap ? updatedIndexTokenCountMap : void 0,
		summary: summaryBoundary ? {
			text: summaryBoundary.text,
			tokenCount: summaryBoundary.tokenCount
		} : void 0,
		boundaryTokenAdjustment
	};
};
/**
* Adds a value at key 0 for system messages and shifts all key indices by one in an indexTokenCountMap.
* This is useful when adding a system message at the beginning of a conversation.
*
* @param indexTokenCountMap - The original map of message indices to token counts
* @param instructionsTokenCount - The token count for the system message to add at index 0
* @returns A new map with the system message at index 0 and all other indices shifted by 1
*/
function shiftIndexTokenCountMap(indexTokenCountMap, instructionsTokenCount) {
	const shiftedMap = {};
	shiftedMap[0] = instructionsTokenCount;
	for (const [indexStr, tokenCount] of Object.entries(indexTokenCountMap)) {
		const index = Number(indexStr);
		shiftedMap[index + 1] = tokenCount;
	}
	return shiftedMap;
}
/** Block types that contain binary image data and must be preserved structurally. */
const IMAGE_BLOCK_TYPES = new Set(["image_url", "image"]);
/** Checks whether a BaseMessage is a tool-role message. */
const isToolMessage = (m) => m instanceof _langchain_core_messages.ToolMessage || "role" in m && m.role === "tool";
/** Flushes accumulated text chunks into `parts` as a single text block. */
function flushTextChunks(textChunks, parts) {
	if (textChunks.length === 0) return;
	parts.push({
		type: "text",
		text: textChunks.join("\n")
	});
	textChunks.length = 0;
}
/**
* Appends a single message's content to the running `textChunks` / `parts`
* accumulators.  Image blocks are shallow-copied into `parts` as-is so that
* binary data (base64 images) never becomes text tokens.  All other block
* types are serialized to text — unrecognized types are JSON-serialized
* rather than silently dropped.
*
* When `content` is an array containing tool_use blocks, `tool_calls` is NOT
* additionally serialized (avoiding double output).  `tool_calls` is used as
* a fallback when `content` is a plain string or an array with no tool_use.
*/
function appendMessageContent(msg, role, textChunks, parts) {
	const { content } = msg;
	if (typeof content === "string") {
		if (content) textChunks.push(`${role}: ${content}`);
		appendToolCalls(msg, role, textChunks);
		return;
	}
	if (!Array.isArray(content)) {
		appendToolCalls(msg, role, textChunks);
		return;
	}
	let hasToolUseBlock = false;
	for (const block of content) {
		if (IMAGE_BLOCK_TYPES.has(block.type ?? "")) {
			flushTextChunks(textChunks, parts);
			parts.push({ ...block });
			continue;
		}
		if (block.type === "tool_use") {
			hasToolUseBlock = true;
			textChunks.push(`${role}: [tool_use] ${String(block.name ?? "")} ${JSON.stringify(block.input ?? {})}`);
			continue;
		}
		const text = block.text ?? block.input;
		if (typeof text === "string" && text) {
			textChunks.push(`${role}: ${text}`);
			continue;
		}
		if (block.type != null && block.type !== "") textChunks.push(`${role}: [${block.type}] ${JSON.stringify(block)}`);
	}
	if (!hasToolUseBlock) appendToolCalls(msg, role, textChunks);
}
function appendToolCalls(msg, role, textChunks) {
	if (role !== "AI") return;
	const aiMsg = msg;
	if (!aiMsg.tool_calls || aiMsg.tool_calls.length === 0) return;
	for (const tc of aiMsg.tool_calls) textChunks.push(`AI: [tool_call] ${tc.name}(${JSON.stringify(tc.args)})`);
}
/**
* Ensures compatibility when switching from a non-thinking agent to a thinking-enabled agent.
* Converts AI messages with tool calls (that lack thinking/reasoning blocks) into buffer strings,
* avoiding the thinking block signature requirement.
*
* Recognizes the following as valid thinking/reasoning blocks:
* - ContentTypes.THINKING (Anthropic)
* - ContentTypes.REASONING_CONTENT (Bedrock)
* - ContentTypes.REASONING (VertexAI / Google)
* - 'redacted_thinking'
*
* @param messages - Array of messages to process
* @param provider - The provider being used (unused but kept for future compatibility)
* @param config - Optional RunnableConfig for structured agent logging
* @param runStartIndex - Index in `messages` where the CURRENT run's own
*   appended AI/Tool messages begin (i.e. anything at this index or later
*   was just produced by this run's own iterations, not historical
*   context). When provided, AI messages at or after this index are
*   never converted to `[Previous agent context]` placeholders — Claude
*   can validly skip a thinking block before a tool_use (cf. PR #116),
*   so the agent's own in-run iterations must not be misclassified as
*   foreign history. Without the signal the function falls back to its
*   prior heuristic (`chainHasThinkingBlock`), preserving backward
*   compatibility for callers that don't yet pass the boundary.
* @returns The messages array with tool sequences converted to buffer strings if necessary
*/
function ensureThinkingBlockInMessages(messages, _provider, config, runStartIndex) {
	if (messages.length === 0) return messages;
	let lastHumanIndex = -1;
	for (let k = messages.length - 1; k >= 0; k--) {
		const m = messages[k];
		if (!isRecord(m)) {
			warnOnce("format.ensureThinkingBlockInMessages skipped non-record entry");
			continue;
		}
		if (m instanceof _langchain_core_messages.HumanMessage || "role" in m && m.role === "user") {
			lastHumanIndex = k;
			break;
		}
	}
	if (lastHumanIndex === messages.length - 1) return messages;
	const result = lastHumanIndex >= 0 ? messages.slice(0, lastHumanIndex + 1) : [];
	let i = lastHumanIndex + 1;
	while (i < messages.length) {
		const msg = messages[i];
		if (!isRecord(msg)) {
			warnOnce("format.ensureThinkingBlockInMessages skipped non-record entry");
			result.push(msg);
			i++;
			continue;
		}
		if (!(msg instanceof _langchain_core_messages.AIMessage || msg instanceof _langchain_core_messages.AIMessageChunk || "role" in msg && msg.role === "assistant")) {
			result.push(msg);
			i++;
			continue;
		}
		const aiMsg = msg;
		const hasToolCalls = aiMsg.tool_calls && aiMsg.tool_calls.length > 0;
		const contentIsArray = Array.isArray(aiMsg.content);
		let hasToolUse = hasToolCalls ?? false;
		let hasThinkingBlock = false;
		if (contentIsArray && aiMsg.content.length > 0) for (const c of aiMsg.content) {
			if (typeof c !== "object") continue;
			if (c.type === "tool_use") hasToolUse = true;
			else if (c.type === "thinking" || c.type === "reasoning_content" || c.type === "reasoning" || c.type === "redacted_thinking") hasThinkingBlock = true;
			if (hasToolUse && hasThinkingBlock) break;
		}
		if (!hasThinkingBlock && aiMsg.additional_kwargs?.reasoning_content != null) hasThinkingBlock = true;
		if (hasToolUse && !hasThinkingBlock) {
			if (runStartIndex !== void 0 && i >= runStartIndex) {
				result.push(msg);
				i++;
				continue;
			}
			if (chainHasThinkingBlock(messages, i)) {
				result.push(msg);
				i++;
				continue;
			}
			const parts = [];
			const textChunks = ["[Previous agent context]"];
			appendMessageContent(msg, "AI", textChunks, parts);
			let j = i + 1;
			while (j < messages.length && isToolMessage(messages[j])) {
				appendMessageContent(messages[j], "Tool", textChunks, parts);
				j++;
			}
			flushTextChunks(textChunks, parts);
			require_events.emitAgentLog(config, "warn", "format", `ensureThinkingBlockInMessages: injecting [Previous agent context] HumanMessage (${parts.length} msgs at index ${i}, no thinking block in chain)`);
			result.push(withMessageRole(new _langchain_core_messages.HumanMessage({ content: require_langchain.toLangChainContent(parts) }), "user"));
			i = j;
		} else {
			result.push(msg);
			i++;
		}
	}
	return result;
}
/**
* Walks backwards from `currentIndex` through the message array to check
* whether an earlier AI message in the same "chain" (no HumanMessage boundary)
* contains a thinking/reasoning block.
*
* A "chain" is a contiguous sequence of AI + Tool messages with no intervening
* HumanMessage. Bedrock reasoning models produce reasoning on the first AI
* response, then issue follow-up tool calls with `content: ""` and no
* reasoning block. These follow-ups are part of the same thinking-enabled
* turn and should not be converted.
*/
function chainHasThinkingBlock(messages, currentIndex) {
	for (let k = currentIndex - 1; k >= 0; k--) {
		const prev = messages[k];
		if (prev instanceof _langchain_core_messages.HumanMessage || "role" in prev && prev.role === "user") return false;
		if (prev instanceof _langchain_core_messages.AIMessage || prev instanceof _langchain_core_messages.AIMessageChunk || "role" in prev && prev.role === "assistant") {
			const prevAiMsg = prev;
			if (Array.isArray(prevAiMsg.content) && prevAiMsg.content.length > 0) {
				if (prevAiMsg.content.some((c) => typeof c === "object" && (c.type === "thinking" || c.type === "reasoning_content" || c.type === "reasoning" || c.type === "redacted_thinking"))) return true;
			}
			if (prevAiMsg.additional_kwargs?.reasoning_content != null) return true;
		}
	}
	return false;
}
//#endregion
exports.ensureThinkingBlockInMessages = ensureThinkingBlockInMessages;
exports.formatAgentMessages = formatAgentMessages;
exports.formatFromLangChain = formatFromLangChain;
exports.formatLangChainMessages = formatLangChainMessages;
exports.formatMediaMessage = formatMediaMessage;
exports.formatMessage = formatMessage;
exports.labelContentByAgent = labelContentByAgent;
exports.shiftIndexTokenCountMap = shiftIndexTokenCountMap;
exports.withMessageRole = withMessageRole;

//# sourceMappingURL=format.cjs.map