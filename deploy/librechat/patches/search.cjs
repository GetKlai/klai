const require_runtime = require("../../_virtual/_rolldown/runtime.cjs");
const require_utils = require("./utils.cjs");
const require_tavily_search = require("./tavily-search.cjs");
let axios = require("axios");
axios = require_runtime.__toESM(axios, 1);
let _langchain_textsplitters = require("@langchain/textsplitters");
//#region src/tools/search/search.ts
const chunker = {
	cleanText: (text) => {
		if (!text) return "";
		const cleaned = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\\+\n/g, "\n").replace(/[\t ]*\n[\t \n]*/g, "\n").replace(/[ \t]+/g, " ");
		// Strip lone surrogates: text splitter can cut inside a UTF-16 surrogate pair
		// (bold/italic Unicode like LinkedIn fancy text encodes as surrogate pairs)
		const sanitized = cleaned.replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, "");
		if (sanitized.length !== cleaned.length) console.warn("[klai-patch] search.cleanText stripped lone UTF-16 surrogate(s)");
		return sanitized.trim();
	},
	splitText: async (text, options) => {
		const chunkSize = options?.chunkSize ?? 150;
		const chunkOverlap = options?.chunkOverlap ?? 50;
		const chunks = await new _langchain_textsplitters.RecursiveCharacterTextSplitter({
			separators: options?.separators || ["\n\n", "\n"],
			chunkSize,
			chunkOverlap
		}).splitText(text);
		// Strip lone surrogates that may result from splitting inside a surrogate pair
		const sanitizedChunks = chunks.map((chunk) => chunk.replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, ""));
		if (sanitizedChunks.some((chunk, i) => chunk.length !== chunks[i].length)) console.warn("[klai-patch] search.splitText stripped lone UTF-16 surrogate(s)");
		return sanitizedChunks;
	},
	splitTexts: async (texts, options, logger) => {
		const logger_ = logger || require_utils.createDefaultLogger();
		const promises = texts.map((text) => chunker.splitText(text, options).catch((error) => {
			logger_.error("Error splitting text:", error);
			return [text];
		}));
		return Promise.all(promises);
	}
};
const DEFAULT_MAX_CONTENT_LENGTH = 5e4;
/** Resolves the per-source scraped content cap from config, the
* `SEARCH_MAX_CONTENT_LENGTH` env var, or the default (50,000 chars) */
function resolveMaxContentLength(maxContentLength) {
	if (maxContentLength != null && maxContentLength > 0) return maxContentLength;
	const envValue = Number(process.env.SEARCH_MAX_CONTENT_LENGTH);
	if (Number.isFinite(envValue) && envValue > 0) return envValue;
	return DEFAULT_MAX_CONTENT_LENGTH;
}
function truncateContent(content, maxLength) {
	return content.length > maxLength ? content.slice(0, maxLength) : content;
}
function createSourceUpdateCallback(sourceMap) {
	return (link, update) => {
		const source = sourceMap.get(link);
		if (source) sourceMap.set(link, {
			...source,
			...update
		});
	};
}
const getHighlights = async ({ query, content, reranker, topResults = 3, maxContentLength = DEFAULT_MAX_CONTENT_LENGTH, logger }) => {
	const logger_ = logger || require_utils.createDefaultLogger();
	if (!content) {
		logger_.warn("No content provided for highlights");
		return;
	}
	if (!reranker) {
		logger_.warn("No reranker provided for highlights");
		return;
	}
	try {
		const documents = await chunker.splitText(truncateContent(content, maxContentLength));
		if (Array.isArray(documents)) return await reranker.rerank(query, documents, topResults);
		else {
			logger_.error("Expected documents to be an array, got:", typeof documents);
			return;
		}
	} catch (error) {
		logger_.error("Error in content processing:", error);
		return;
	}
};
const createSerperAPI = (apiKey) => {
	const config = {
		apiKey: apiKey ?? process.env.SERPER_API_KEY,
		apiUrl: "https://google.serper.dev/search",
		timeout: 1e4
	};
	if (config.apiKey == null || config.apiKey === "") throw new Error("SERPER_API_KEY is required for SerperAPI");
	const getSources = async ({ query, date, country, safeSearch, numResults = 5, type }) => {
		if (!query.trim()) return {
			success: false,
			error: "Query cannot be empty"
		};
		try {
			const payload = {
				q: query,
				safe: [
					"off",
					"moderate",
					"active"
				][safeSearch ?? 1],
				num: Math.min(Math.max(1, numResults), 10)
			};
			if (type) payload.type = type;
			if (date != null) payload.tbs = `qdr:${date}`;
			if (country != null && country !== "") payload["gl"] = country.toLowerCase();
			let apiEndpoint = config.apiUrl;
			if (type === "images") apiEndpoint = "https://google.serper.dev/images";
			else if (type === "videos") apiEndpoint = "https://google.serper.dev/videos";
			else if (type === "news") apiEndpoint = "https://google.serper.dev/news";
			const data = (await axios.default.post(apiEndpoint, payload, {
				headers: {
					"X-API-KEY": config.apiKey,
					"Content-Type": "application/json"
				},
				timeout: config.timeout
			})).data;
			return {
				success: true,
				data: {
					organic: data.organic,
					images: data.images ?? [],
					answerBox: data.answerBox,
					topStories: data.topStories ?? [],
					peopleAlsoAsk: data.peopleAlsoAsk,
					knowledgeGraph: data.knowledgeGraph,
					relatedSearches: data.relatedSearches,
					videos: data.videos ?? [],
					news: data.news ?? []
				}
			};
		} catch (error) {
			return {
				success: false,
				error: `API request failed: ${error instanceof Error ? error.message : String(error)}`
			};
		}
	};
	return { getSources };
};
const createSearXNGAPI = (instanceUrl, apiKey) => {
	const config = {
		instanceUrl: instanceUrl ?? process.env.SEARXNG_INSTANCE_URL,
		apiKey: apiKey ?? process.env.SEARXNG_API_KEY,
		defaultLocation: "all",
		timeout: 1e4
	};
	if (config.instanceUrl == null || config.instanceUrl === "") throw new Error("SEARXNG_INSTANCE_URL is required for SearXNG API");
	const getSources = async ({ query, numResults = 5, safeSearch, type }) => {
		if (!query.trim()) return {
			success: false,
			error: "Query cannot be empty"
		};
		try {
			if (config.instanceUrl == null || config.instanceUrl === "") return {
				success: false,
				error: "Instance URL is not defined"
			};
			let searchUrl = config.instanceUrl;
			if (!searchUrl.endsWith("/search")) searchUrl = searchUrl.replace(/\/$/, "") + "/search";
			let category = "general";
			if (type === "images") category = "images";
			else if (type === "videos") category = "videos";
			else if (type === "news") category = "news";
			const params = {
				q: query,
				format: "json",
				pageno: 1,
				categories: category,
				language: "all",
				safesearch: safeSearch,
				engines: "google,bing,duckduckgo"
			};
			const headers = { "Content-Type": "application/json" };
			if (config.apiKey != null && config.apiKey !== "") headers["X-API-Key"] = config.apiKey;
			const data = (await axios.default.get(searchUrl, {
				headers,
				params,
				timeout: config.timeout
			})).data;
			const isNewsResult = (result) => {
				const url = result.url?.toLowerCase() ?? "";
				const title = result.title?.toLowerCase() ?? "";
				const hasNewsKeywords = [
					"breaking news",
					"latest news",
					"top stories",
					"news today",
					"developing story",
					"trending news",
					"news"
				].some((keyword) => title.toLowerCase().includes(keyword));
				const hasNewsPath = url.includes("/news/") || url.includes("/world/") || url.includes("/politics/") || url.includes("/breaking/");
				return hasNewsKeywords || hasNewsPath;
			};
			const organicResults = (data.results ?? []).slice(0, numResults).map((result, index) => {
				let attribution = "";
				try {
					attribution = new URL(result.url ?? "").hostname;
				} catch {
					attribution = "";
				}
				return {
					position: index + 1,
					title: result.title ?? "",
					link: result.url ?? "",
					snippet: result.content ?? "",
					date: result.publishedDate ?? "",
					attribution
				};
			});
			const imageResults = (data.results ?? []).filter((result) => result.img_src).slice(0, 6).map((result, index) => ({
				title: result.title ?? "",
				imageUrl: result.img_src ?? "",
				position: index + 1,
				source: new URL(result.url ?? "").hostname,
				domain: new URL(result.url ?? "").hostname,
				link: result.url ?? ""
			}));
			const newsResults = (data.results ?? []).filter(isNewsResult).map((result, index) => {
				let attribution = "";
				try {
					attribution = new URL(result.url ?? "").hostname;
				} catch {
					attribution = "";
				}
				return {
					title: result.title ?? "",
					link: result.url ?? "",
					snippet: result.content ?? "",
					date: result.publishedDate ?? "",
					source: attribution,
					imageUrl: result.img_src ?? "",
					position: index + 1
				};
			});
			return {
				success: true,
				data: {
					organic: organicResults,
					images: imageResults,
					topStories: newsResults.slice(0, 5),
					relatedSearches: Array.isArray(data.suggestions) ? data.suggestions.map((suggestion) => ({ query: suggestion })) : [],
					videos: [],
					news: newsResults,
					places: [],
					shopping: [],
					peopleAlsoAsk: [],
					knowledgeGraph: void 0,
					answerBox: void 0
				}
			};
		} catch (error) {
			return {
				success: false,
				error: `SearXNG API request failed: ${error instanceof Error ? error.message : String(error)}`
			};
		}
	};
	return { getSources };
};
const createSearchAPI = (config) => {
	const { searchProvider = "serper", serperApiKey, searxngInstanceUrl, searxngApiKey, tavilyApiKey, tavilySearchUrl, tavilySearchOptions } = config;
	if (searchProvider.toLowerCase() === "serper") return createSerperAPI(serperApiKey);
	else if (searchProvider.toLowerCase() === "searxng") return createSearXNGAPI(searxngInstanceUrl, searxngApiKey);
	else if (searchProvider.toLowerCase() === "tavily") return require_tavily_search.createTavilyAPI(tavilyApiKey, tavilySearchUrl, tavilySearchOptions);
	else throw new Error(`Invalid search provider: ${searchProvider}. Must be 'serper', 'searxng', or 'tavily'`);
};
const createSourceProcessor = (config = {}, scraperInstance) => {
	if (!scraperInstance) throw new Error("Scraper instance is required");
	const { topResults = 3, reranker, logger } = config;
	const maxContentLength = resolveMaxContentLength(config.maxContentLength);
	const logger_ = logger || require_utils.createDefaultLogger();
	const scraper = scraperInstance;
	const processResponse = (url, response) => {
		const rawMetadata = scraper.extractMetadata(response);
		const attribution = require_utils.getAttribution(url, Object.keys(rawMetadata).length > 0 ? rawMetadata : void 0, logger_);
		if (response.success && response.data) {
			const [content, references] = scraper.extractContent(response);
			return {
				url,
				references,
				attribution,
				content: truncateContent(chunker.cleanText(content), maxContentLength)
			};
		}
		logger_.error(`Error scraping ${url}: ${response.error ?? "Unknown error"}`);
		return {
			url,
			attribution,
			error: true,
			content: ""
		};
	};
	const addHighlights = async (result, query, onGetHighlights) => {
		if (result.error != null) return result;
		try {
			const highlights = await getHighlights({
				query,
				reranker,
				content: result.content,
				maxContentLength,
				logger: logger_
			});
			if (onGetHighlights) onGetHighlights(result.url);
			return {
				...result,
				highlights
			};
		} catch (error) {
			logger_.error("Error processing scraped content:", error);
			return result;
		}
	};
	const webScraper = { scrapeMany: async ({ query, links, onGetHighlights }) => {
		logger_.debug(`Scraping ${links.length} links`);
		try {
			let responses;
			if (scraper.scrapeUrls) responses = await scraper.scrapeUrls(links);
			else responses = await Promise.all(links.map((link) => scraper.scrapeUrl(link, {}).catch((error) => {
				logger_.error(`Error scraping ${link}:`, error);
				return [link, {
					success: false,
					error: String(error)
				}];
			})));
			return await Promise.all(responses.map(([url, response]) => addHighlights(processResponse(url, response), query, onGetHighlights)));
		} catch (error) {
			logger_.error("Error in scrapeMany:", error);
			return [];
		}
	} };
	const fetchContents = async ({ links, query, target, onGetHighlights, onContentScraped }) => {
		const initialLinks = links.slice(0, target);
		const results = await webScraper.scrapeMany({
			query,
			links: initialLinks,
			onGetHighlights
		});
		for (const result of results) {
			if (result.error === true) continue;
			const { url, content, attribution, references, highlights } = result;
			onContentScraped?.(url, {
				content,
				attribution,
				references,
				highlights
			});
		}
	};
	const processSources = async ({ result, numElements, query, news, proMode = true, onGetHighlights }) => {
		try {
			if (!result.data) return {
				organic: [],
				topStories: [],
				images: [],
				relatedSearches: []
			};
			if (result.data.topStories != null && result.data.topStories.length > numElements)
 /** Merged news results can far exceed the requested source count;
			* every entry is formatted into the LLM output, so cap them up
			* front — before any early return below and before scraping
			* entries the cap would discard */
			result.data.topStories = result.data.topStories.slice(0, numElements);
			if (!result.data.organic) return result.data;
			if (!proMode) {
				const wikiSources = result.data.organic.filter((source) => source.link.includes("wikipedia.org"));
				if (!wikiSources.length) return result.data;
				const wikiSourceMap = /* @__PURE__ */ new Map();
				wikiSourceMap.set(wikiSources[0].link, wikiSources[0]);
				await fetchContents({
					query,
					target: 1,
					onGetHighlights,
					onContentScraped: createSourceUpdateCallback(wikiSourceMap),
					links: [wikiSources[0].link]
				});
				for (let i = 0; i < result.data.organic.length; i++) {
					const source = result.data.organic[i];
					const updatedSource = wikiSourceMap.get(source.link);
					if (updatedSource) result.data.organic[i] = {
						...source,
						...updatedSource
					};
				}
				return result.data;
			}
			const sourceMap = /* @__PURE__ */ new Map();
			const organicLinksSet = /* @__PURE__ */ new Set();
			const organicLinks = collectLinks(result.data.organic, sourceMap, organicLinksSet);
			const topStories = result.data.topStories ?? [];
			const topStoryLinks = collectLinks(topStories, sourceMap, organicLinksSet);
			if (organicLinks.length === 0 && (topStoryLinks.length === 0 || !news)) return result.data;
			const onContentScraped = createSourceUpdateCallback(sourceMap);
			const promises = [];
			if (organicLinks.length > 0) promises.push(fetchContents({
				query,
				onGetHighlights,
				onContentScraped,
				links: organicLinks,
				target: numElements
			}));
			if (news && topStoryLinks.length > 0) promises.push(fetchContents({
				query,
				onGetHighlights,
				onContentScraped,
				links: topStoryLinks,
				target: numElements
			}));
			await Promise.all(promises);
			if (result.data.organic.length > 0) updateSourcesWithContent(result.data.organic, sourceMap);
			if (news && topStories.length > 0) updateSourcesWithContent(topStories, sourceMap);
			return result.data;
		} catch (error) {
			logger_.error("Error in processSources:", error);
			return {
				organic: [],
				topStories: [],
				images: [],
				relatedSearches: [],
				...result.data,
				error: error instanceof Error ? error.message : String(error)
			};
		}
	};
	return {
		processSources,
		topResults
	};
};
/** Helper function to collect links and update sourceMap */
function collectLinks(sources, sourceMap, existingLinksSet) {
	const links = [];
	for (const source of sources) if (source.link) {
		if (existingLinksSet && existingLinksSet.has(source.link)) continue;
		links.push(source.link);
		if (existingLinksSet) existingLinksSet.add(source.link);
		sourceMap.set(source.link, source);
	}
	return links;
}
/** Helper function to update sources with scraped content */
function updateSourcesWithContent(sources, sourceMap) {
	for (let i = 0; i < sources.length; i++) {
		const source = sources[i];
		const updatedSource = sourceMap.get(source.link);
		if (updatedSource) sources[i] = {
			...source,
			...updatedSource
		};
	}
}
//#endregion
exports.createSearchAPI = createSearchAPI;
exports.createSourceProcessor = createSourceProcessor;

//# sourceMappingURL=search.cjs.map