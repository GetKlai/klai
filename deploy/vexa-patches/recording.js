"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.startGoogleRecording = startGoogleRecording;
const utils_1 = require("../../utils");
const whisperlive_1 = require("../../services/whisperlive");
const recording_1 = require("../../services/recording");
const index_1 = require("../../index");
const injection_1 = require("../../utils/injection");
const selectors_1 = require("./selectors");
// Modified to use new services - Google Meet recording functionality
async function startGoogleRecording(page, botConfig) {
    const transcriptionEnabled = botConfig.transcribeEnabled !== false;
    let whisperLiveService = null;
    let whisperLiveUrl = null;
    if (transcriptionEnabled) {
        whisperLiveService = new whisperlive_1.WhisperLiveService({
            whisperLiveUrl: process.env.WHISPER_LIVE_URL
        });
        // Initialize WhisperLive connection with STUBBORN reconnection - NEVER GIVES UP!
        whisperLiveUrl = await whisperLiveService.initializeWithStubbornReconnection("Google Meet");
        (0, utils_1.log)(`[Node.js] Using WhisperLive URL for Google Meet: ${whisperLiveUrl}`);
    }
    else {
        (0, utils_1.log)("[Google Recording] Transcription disabled by config; running recording-only mode.");
    }
    (0, utils_1.log)("Starting Google Meet recording with WebSocket connection");
    const wantsAudioCapture = !!botConfig.recordingEnabled &&
        (!Array.isArray(botConfig.captureModes) || botConfig.captureModes.includes("audio"));
    const sessionUid = botConfig.connectionId || `gm-${Date.now()}`;
    let recordingService = null;
    if (wantsAudioCapture) {
        recordingService = new recording_1.RecordingService(botConfig.meeting_id, sessionUid);
        (0, index_1.setActiveRecordingService)(recordingService);
        await page.exposeFunction("__vexaSaveRecordingBlob", async (payload) => {
            try {
                if (!recordingService) {
                    (0, utils_1.log)("[Google Recording] Recording service not initialized; dropping blob.");
                    return false;
                }
                const mimeType = (payload?.mimeType || "").toLowerCase();
                let format = "webm";
                if (mimeType.includes("wav"))
                    format = "wav";
                else if (mimeType.includes("ogg"))
                    format = "ogg";
                else if (mimeType.includes("mp4") || mimeType.includes("m4a"))
                    format = "m4a";
                const blobBuffer = Buffer.from(payload.base64 || "", "base64");
                if (!blobBuffer.length) {
                    (0, utils_1.log)("[Google Recording] Received empty audio blob.");
                    return false;
                }
                await recordingService.writeBlob(blobBuffer, format);
                (0, utils_1.log)(`[Google Recording] Saved browser audio blob (${blobBuffer.length} bytes, ${format}).`);
                return true;
            }
            catch (error) {
                (0, utils_1.log)(`[Google Recording] Failed to persist browser blob: ${error?.message || String(error)}`);
                return false;
            }
        });
    }
    else {
        (0, utils_1.log)("[Google Recording] Audio capture disabled by config.");
    }
    await (0, injection_1.ensureBrowserUtils)(page, require('path').join(__dirname, '../../browser-utils.global.js'));
    // Pass the necessary config fields and the resolved URL into the page context
    await page.evaluate(async (pageArgs) => {
        const { botConfigData, whisperUrlForBrowser, selectors } = pageArgs;
        const transcriptionEnabled = botConfigData?.transcribeEnabled !== false;
        // Use browser utility classes from the global bundle
        const browserUtils = window.VexaBrowserUtils;
        window.logBot(`Browser utils available: ${Object.keys(browserUtils || {}).join(', ')}`);
        // --- Early reconfigure wiring (stub + event) ---
        // Queue reconfig requests until service is ready
        window.__vexaPendingReconfigure = null;
        if (typeof window.triggerWebSocketReconfigure !== 'function') {
            window.triggerWebSocketReconfigure = async (lang, task) => {
                window.__vexaPendingReconfigure = { lang, task };
                window.logBot?.('[Reconfigure] Stub queued update; will apply when service is ready.');
            };
        }
        try {
            document.addEventListener('vexa:reconfigure', (ev) => {
                try {
                    const detail = ev.detail || {};
                    const { lang, task } = detail;
                    const fn = window.triggerWebSocketReconfigure;
                    if (typeof fn === 'function')
                        fn(lang, task);
                }
                catch { }
            });
        }
        catch { }
        // ---------------------------------------------
        const audioService = new browserUtils.BrowserAudioService({
            targetSampleRate: 16000,
            bufferSize: 4096,
            inputChannels: 1,
            outputChannels: 1
        });
        // Use BrowserWhisperLiveService with stubborn mode to enable reconnection on Google Meet
        const whisperLiveService = transcriptionEnabled
            ? new browserUtils.BrowserWhisperLiveService({
                whisperLiveUrl: whisperUrlForBrowser
            }, true) // Enable stubborn mode for Google Meet
            : null;
        // Expose references for reconfiguration
        window.__vexaWhisperLiveService = whisperLiveService;
        window.__vexaAudioService = audioService;
        window.__vexaBotConfig = botConfigData;
        window.__vexaMediaRecorder = null;
        window.__vexaRecordedChunks = [];
        window.__vexaRecordingFlushed = false;
        const isAudioRecordingEnabled = !!botConfigData?.recordingEnabled &&
            (!Array.isArray(botConfigData?.captureModes) ||
                botConfigData?.captureModes.includes("audio"));
        const getSupportedMediaRecorderMimeType = () => {
            const candidates = [
                "audio/webm;codecs=opus",
                "audio/webm",
                "audio/ogg;codecs=opus",
                "audio/ogg",
            ];
            for (const mime of candidates) {
                try {
                    if (window.MediaRecorder?.isTypeSupported?.(mime)) {
                        return mime;
                    }
                }
                catch { }
            }
            return "";
        };
        const flushBrowserRecordingBlob = async (reason) => {
            if (!isAudioRecordingEnabled)
                return;
            if (window.__vexaRecordingFlushed)
                return;
            try {
                const recorder = window.__vexaMediaRecorder;
                const chunks = window.__vexaRecordedChunks || [];
                const finalizeAndSend = async () => {
                    if (window.__vexaRecordingFlushed)
                        return;
                    window.__vexaRecordingFlushed = true;
                    try {
                        const recorded = window.__vexaRecordedChunks || [];
                        if (!recorded.length) {
                            window.logBot?.(`[Google Recording] No media chunks to flush (${reason}).`);
                            return;
                        }
                        const mimeType = window.__vexaMediaRecorder?.mimeType || "audio/webm";
                        const blob = new Blob(recorded, { type: mimeType });
                        const buffer = await blob.arrayBuffer();
                        const bytes = new Uint8Array(buffer);
                        let binary = "";
                        const chunkSize = 0x8000;
                        for (let i = 0; i < bytes.length; i += chunkSize) {
                            binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
                        }
                        const base64 = btoa(binary);
                        if (typeof window.__vexaSaveRecordingBlob === "function") {
                            await window.__vexaSaveRecordingBlob({
                                base64,
                                mimeType: blob.type || mimeType,
                            });
                            window.logBot?.(`[Google Recording] Flushed ${bytes.length} bytes (${blob.type || mimeType}) on ${reason}.`);
                        }
                        else {
                            window.logBot?.("[Google Recording] Node blob sink is not available.");
                        }
                    }
                    catch (err) {
                        window.logBot?.(`[Google Recording] Failed to flush blob: ${err?.message || err}`);
                    }
                    finally {
                        window.__vexaRecordedChunks = [];
                    }
                };
                if (recorder && recorder.state !== "inactive") {
                    await new Promise((resolveStop) => {
                        const onStop = async () => {
                            recorder.removeEventListener("stop", onStop);
                            await finalizeAndSend();
                            resolveStop();
                        };
                        recorder.addEventListener("stop", onStop, { once: true });
                        try {
                            recorder.stop();
                        }
                        catch {
                            // Recorder may already be stopping; resolve after a short delay.
                            setTimeout(async () => {
                                await finalizeAndSend();
                                resolveStop();
                            }, 200);
                        }
                    });
                }
                else if (chunks.length > 0) {
                    await finalizeAndSend();
                }
            }
            catch (err) {
                window.logBot?.(`[Google Recording] Unexpected flush error: ${err?.message || err}`);
            }
        };
        window.__vexaFlushRecordingBlob = flushBrowserRecordingBlob;
        // Replace stub with real reconfigure implementation and apply any queued update
        window.triggerWebSocketReconfigure = async (lang, task) => {
            try {
                const svc = window.__vexaWhisperLiveService;
                if (!transcriptionEnabled) {
                    window.logBot?.('[Reconfigure] Ignored because transcription is disabled.');
                    return;
                }
                const cfg = window.__vexaBotConfig || {};
                cfg.language = lang;
                cfg.task = task || 'transcribe';
                window.__vexaBotConfig = cfg;
                // Close existing connection to establish new session from scratch
                window.logBot?.(`[Reconfigure] Closing existing connection to establish new session...`);
                try {
                    // Use closeForReconfigure to prevent auto-reconnect during manual reconfigure
                    if (svc?.closeForReconfigure) {
                        svc.closeForReconfigure();
                    }
                    else {
                        svc?.close();
                    }
                    // Reset audio service session start time so speaker events use new session timestamps
                    const audioSvc = window.__vexaAudioService;
                    if (audioSvc?.resetSessionStartTime) {
                        audioSvc.resetSessionStartTime();
                    }
                    // Wait a brief moment to ensure socket is fully closed
                    await new Promise(resolve => setTimeout(resolve, 100));
                }
                catch (closeErr) {
                    window.logBot?.(`[Reconfigure] Error closing connection: ${closeErr?.message || closeErr}`);
                }
                // Reconnect with new config - this will generate a new session_uid
                window.logBot?.(`[Reconfigure] Reconnecting with new config: language=${cfg.language}, task=${cfg.task}`);
                await svc?.connectToWhisperLive(cfg, window.__vexaOnMessage, window.__vexaOnError, window.__vexaOnClose);
                window.logBot?.(`[Reconfigure] Successfully reconnected with new session. Language=${cfg.language}, Task=${cfg.task}`);
            }
            catch (e) {
                window.logBot?.(`[Reconfigure] Error applying new config: ${e?.message || e}`);
            }
        };
        try {
            const pending = window.__vexaPendingReconfigure;
            if (pending && typeof window.triggerWebSocketReconfigure === 'function') {
                window.triggerWebSocketReconfigure(pending.lang, pending.task);
                window.__vexaPendingReconfigure = null;
            }
        }
        catch { }
        await new Promise((resolve, reject) => {
            try {
                window.logBot("Starting Google Meet recording process with new services.");
                // Wait a bit for media elements to initialize after admission, then start the chain
                (async () => {
                    let degradedNoMedia = false;
                    // Wait 2 seconds for media elements to initialize after admission
                    window.logBot("Waiting 2 seconds for media elements to initialize after admission...");
                    await new Promise(resolve => setTimeout(resolve, 2000));
                    // Find and create combined audio stream with enhanced retry logic
                    // Use 10 retries with 3s delay = 30s total wait time
                    audioService.findMediaElements(10, 3000).then(async (mediaElements) => {
                        if (mediaElements.length === 0) {
                            degradedNoMedia = true;
                            window.logBot("[Google Meet BOT Warning] No active media elements found after retries; " +
                                "continuing in degraded monitoring mode (session remains active).");
                            return undefined;
                        }
                        // Create combined audio stream
                        return await audioService.createCombinedAudioStream(mediaElements);
                    }).then(async (combinedStream) => {
                        if (!combinedStream) {
                            if (!degradedNoMedia) {
                                reject(new Error("[Google Meet BOT Error] Failed to create combined audio stream"));
                                return;
                            }
                            return null;
                        }
                        if (isAudioRecordingEnabled) {
                            try {
                                const mimeType = getSupportedMediaRecorderMimeType();
                                const recorderOptions = mimeType ? { mimeType } : undefined;
                                const recorder = recorderOptions
                                    ? new MediaRecorder(combinedStream, recorderOptions)
                                    : new MediaRecorder(combinedStream);
                                window.__vexaMediaRecorder = recorder;
                                window.__vexaRecordedChunks = [];
                                window.__vexaRecordingFlushed = false;
                                recorder.ondataavailable = (event) => {
                                    if (event.data && event.data.size > 0) {
                                        window.__vexaRecordedChunks.push(event.data);
                                    }
                                };
                                recorder.start(1000);
                                window.logBot?.(`[Google Recording] MediaRecorder started (${recorder.mimeType || mimeType || "default"}).`);
                            }
                            catch (err) {
                                window.logBot?.(`[Google Recording] Failed to start MediaRecorder: ${err?.message || err}`);
                            }
                        }
                        // Initialize audio processor
                        return await audioService.initializeAudioProcessor(combinedStream);
                    }).then(async (processor) => {
                        if (!processor) {
                            return null;
                        }
                        // Setup audio data processing
                        audioService.setupAudioDataProcessor(async (audioData, sessionStartTime) => {
                            if (!transcriptionEnabled || !whisperLiveService) {
                                return;
                            }
                            // Only send after server ready (canonical Teams pattern)
                            if (!whisperLiveService.isReady()) {
                                // Skip sending until server is ready
                                return;
                            }
                            // Compute simple RMS and peak for diagnostics
                            let sumSquares = 0;
                            let peak = 0;
                            for (let i = 0; i < audioData.length; i++) {
                                const v = audioData[i];
                                sumSquares += v * v;
                                const a = Math.abs(v);
                                if (a > peak)
                                    peak = a;
                            }
                            const rms = Math.sqrt(sumSquares / Math.max(1, audioData.length));
                            // Diagnostic: send metadata first
                            whisperLiveService.sendAudioChunkMetadata(audioData.length, 16000);
                            // Send audio data to WhisperLive
                            const success = whisperLiveService.sendAudioData(audioData);
                            if (!success) {
                                window.logBot("Failed to send Google Meet audio data to WhisperLive");
                            }
                        });
                        // Initialize WhisperLive WebSocket connection with simple reconnection wrapper
                        const connectWhisper = async () => {
                            if (!transcriptionEnabled || !whisperLiveService) {
                                return;
                            }
                            try {
                                // Define callbacks so they can be reused for reconfiguration reconnects
                                const onMessage = (data) => {
                                    const logFn = window.logBot;
                                    // Reduce log spam: log only important status changes and completed transcript segments
                                    if (!data || typeof data !== 'object') {
                                        return;
                                    }
                                    if (data["status"] === "ERROR") {
                                        logFn(`Google Meet WebSocket Server Error: ${data["message"]}`);
                                        return;
                                    }
                                    if (data["status"] === "WAIT") {
                                        logFn(`Google Meet Server busy: ${data["message"]}`);
                                        return;
                                    }
                                    if (!whisperLiveService.isReady() && data["status"] === "SERVER_READY") {
                                        whisperLiveService.setServerReady(true);
                                        logFn("Google Meet Server is ready.");
                                        return;
                                    }
                                    if (data["language"]) {
                                        if (!window.__vexaLangLogged) {
                                            window.__vexaLangLogged = true;
                                            logFn(`Google Meet Language detected: ${data["language"]}`);
                                        }
                                        // do not return; language can accompany segments
                                    }
                                    if (data["message"] === "DISCONNECT") {
                                        logFn("Google Meet Server requested disconnect.");
                                        whisperLiveService.close();
                                        return;
                                    }
                                    // Log only completed transcript segments, with deduplication
                                    if (Array.isArray(data.segments)) {
                                        const completedTexts = data.segments
                                            .filter((s) => s && s.completed && s.text)
                                            .map((s) => s.text);
                                        if (completedTexts.length > 0) {
                                            const transcriptKey = completedTexts.join(' ').trim();
                                            if (transcriptKey && transcriptKey !== window.__lastTranscript) {
                                                window.__lastTranscript = transcriptKey;
                                                logFn(`Transcript: ${transcriptKey}`);
                                            }
                                        }
                                    }
                                };
                                const onError = (event) => {
                                    window.logBot(`[Google Meet Failover] WebSocket error. This will trigger retry logic.`);
                                };
                                const onClose = async (event) => {
                                    window.logBot(`[Google Meet Failover] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}. Attempting reconnect in 2s...`);
                                    try {
                                        whisperLiveService.setServerReady(false);
                                    }
                                    catch { }
                                    setTimeout(() => {
                                        // Best-effort reconnect; BrowserWhisperLiveService stubborn mode should also help
                                        connectWhisper().catch(() => { });
                                    }, 2000);
                                };
                                // Save callbacks globally for reuse
                                window.__vexaOnMessage = onMessage;
                                window.__vexaOnError = onError;
                                window.__vexaOnClose = onClose;
                                await whisperLiveService.connectToWhisperLive(window.__vexaBotConfig, onMessage, onError, onClose);
                            }
                            catch (e) {
                                window.logBot(`Google Meet connect error: ${e?.message || e}. Retrying in 2s...`);
                                setTimeout(() => { connectWhisper().catch(() => { }); }, 2000);
                            }
                        };
                        return await connectWhisper();
                    }).then(() => {
                        // Initialize Google-specific speaker detection (Teams-style with Google selectors)
                        if (!degradedNoMedia) {
                            window.logBot("Initializing Google Meet speaker detection...");
                        }
                        const initializeGoogleSpeakerDetection = (whisperLiveService, audioService, botConfigData) => {
                            const selectorsTyped = selectors;
                            const speakingStates = new Map();
                            function hashStr(s) {
                                // small non-crypto hash to avoid logging PII
                                let h = 5381;
                                for (let i = 0; i < s.length; i++)
                                    h = ((h << 5) + h) ^ s.charCodeAt(i);
                                return (h >>> 0).toString(16).slice(0, 8);
                            }
                            function getGoogleParticipantId(element) {
                                let id = element.getAttribute('data-participant-id');
                                if (!id) {
                                    const stableChild = element.querySelector('[jsinstance]');
                                    if (stableChild) {
                                        id = stableChild.getAttribute('jsinstance') || undefined;
                                    }
                                }
                                if (!id) {
                                    if (!element.dataset.vexaGeneratedId) {
                                        element.dataset.vexaGeneratedId = 'gm-id-' + Math.random().toString(36).substr(2, 9);
                                    }
                                    id = element.dataset.vexaGeneratedId;
                                }
                                return id;
                            }
                            function getGoogleParticipantName(participantElement) {
                                // Prefer explicit Meet name spans
                                const notranslate = participantElement.querySelector('span.notranslate');
                                if (notranslate && notranslate.textContent && notranslate.textContent.trim()) {
                                    const t = notranslate.textContent.trim();
                                    if (t.length > 1 && t.length < 50)
                                        return t;
                                }
                                // Try configured name selectors
                                const nameSelectors = selectorsTyped.nameSelectors || [];
                                for (const sel of nameSelectors) {
                                    const el = participantElement.querySelector(sel);
                                    if (el) {
                                        let nameText = el.textContent || el.innerText || el.getAttribute('data-self-name') || el.getAttribute('aria-label') || '';
                                        if (nameText) {
                                            nameText = nameText.trim();
                                            if (nameText && nameText.length > 1 && nameText.length < 50)
                                                return nameText;
                                        }
                                    }
                                }
                                // Fallbacks
                                const selfName = participantElement.getAttribute('data-self-name');
                                if (selfName && selfName.trim())
                                    return selfName.trim();
                                const idToDisplay = getGoogleParticipantId(participantElement);
                                return `Google Participant (${idToDisplay})`;
                            }
                            function isVisible(el) {
                                const cs = getComputedStyle(el);
                                const rect = el.getBoundingClientRect();
                                const ariaHidden = el.getAttribute('aria-hidden') === 'true';
                                return (rect.width > 0 &&
                                    rect.height > 0 &&
                                    cs.display !== 'none' &&
                                    cs.visibility !== 'hidden' &&
                                    cs.opacity !== '0' &&
                                    !ariaHidden);
                            }
                            function hasSpeakingIndicator(container) {
                                const indicators = selectorsTyped.speakingIndicators || [];
                                for (const sel of indicators) {
                                    const ind = container.querySelector(sel);
                                    if (ind && isVisible(ind))
                                        return true;
                                }
                                return false;
                            }
                            function inferSpeakingFromClasses(container, mutatedClassList) {
                                const speakingClasses = selectorsTyped.speakingClasses || [];
                                const silenceClasses = selectorsTyped.silenceClasses || [];
                                const classList = mutatedClassList || container.classList;
                                const descendantSpeaking = speakingClasses.some(cls => container.querySelector('.' + cls));
                                const hasSpeaking = speakingClasses.some(cls => classList.contains(cls)) || descendantSpeaking;
                                const hasSilent = silenceClasses.some(cls => classList.contains(cls));
                                if (hasSpeaking)
                                    return { speaking: true };
                                if (hasSilent)
                                    return { speaking: false };
                                return { speaking: false };
                            }
                            function sendGoogleSpeakerEvent(eventType, participantElement) {
                                const sessionStartTime = audioService.getSessionAudioStartTime();
                                if (sessionStartTime === null) {
                                    return;
                                }
                                const relativeTimestampMs = Date.now() - sessionStartTime;
                                const participantId = getGoogleParticipantId(participantElement);
                                const participantName = getGoogleParticipantName(participantElement);
                                try {
                                    whisperLiveService.sendSpeakerEvent(eventType, participantName, participantId, relativeTimestampMs, botConfigData);
                                }
                                catch { }
                            }
                            function logGoogleSpeakerEvent(participantElement, mutatedClassList) {
                                const participantId = getGoogleParticipantId(participantElement);
                                const participantName = getGoogleParticipantName(participantElement);
                                const previousLogicalState = speakingStates.get(participantId) || 'silent';
                                // Primary: indicators; Fallback: classes
                                const indicatorSpeaking = hasSpeakingIndicator(participantElement);
                                const classInference = inferSpeakingFromClasses(participantElement, mutatedClassList);
                                const isCurrentlySpeaking = indicatorSpeaking || classInference.speaking;
                                if (isCurrentlySpeaking) {
                                    if (previousLogicalState !== 'speaking') {
                                        window.logBot(`🎤 [Google] SPEAKER_START: ${participantName} (ID: ${participantId})`);
                                        sendGoogleSpeakerEvent('SPEAKER_START', participantElement);
                                    }
                                    speakingStates.set(participantId, 'speaking');
                                }
                                else {
                                    if (previousLogicalState === 'speaking') {
                                        window.logBot(`🔇 [Google] SPEAKER_END: ${participantName} (ID: ${participantId})`);
                                        sendGoogleSpeakerEvent('SPEAKER_END', participantElement);
                                    }
                                    speakingStates.set(participantId, 'silent');
                                }
                            }
                            function observeGoogleParticipant(participantElement) {
                                const participantId = getGoogleParticipantId(participantElement);
                                speakingStates.set(participantId, 'silent');
                                // Initial scan
                                logGoogleSpeakerEvent(participantElement);
                                const callback = function (mutationsList) {
                                    for (const mutation of mutationsList) {
                                        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                                            const targetElement = mutation.target;
                                            if (participantElement.contains(targetElement) || participantElement === targetElement) {
                                                logGoogleSpeakerEvent(participantElement, targetElement.classList);
                                            }
                                        }
                                    }
                                };
                                const observer = new MutationObserver(callback);
                                observer.observe(participantElement, {
                                    attributes: true,
                                    attributeFilter: ['class'],
                                    subtree: true
                                });
                                if (!participantElement.dataset.vexaObserverAttached) {
                                    participantElement.dataset.vexaObserverAttached = 'true';
                                }
                            }
                            function scanForAllGoogleParticipants() {
                                const participantSelectors = selectorsTyped.participantSelectors || [];
                                for (const sel of participantSelectors) {
                                    document.querySelectorAll(sel).forEach((el) => {
                                        const elh = el;
                                        if (!elh.dataset.vexaObserverAttached) {
                                            observeGoogleParticipant(elh);
                                        }
                                    });
                                }
                            }
                            // Attempt to click People button to stabilize DOM if available
                            try {
                                const peopleSelectors = selectorsTyped.peopleButtonSelectors || [];
                                for (const sel of peopleSelectors) {
                                    const btn = document.querySelector(sel);
                                    if (btn && isVisible(btn)) {
                                        btn.click();
                                        break;
                                    }
                                }
                            }
                            catch { }
                            // Initialize
                            scanForAllGoogleParticipants();
                            // Polling fallback to catch speaking indicators not driven by class mutations
                            const lastSpeakingById = new Map();
                            setInterval(() => {
                                const participantSelectors = selectorsTyped.participantSelectors || [];
                                const elements = [];
                                participantSelectors.forEach(sel => {
                                    document.querySelectorAll(sel).forEach(el => elements.push(el));
                                });
                                elements.forEach((container) => {
                                    const id = getGoogleParticipantId(container);
                                    const indicatorSpeaking = hasSpeakingIndicator(container) || inferSpeakingFromClasses(container).speaking;
                                    const prev = lastSpeakingById.get(id) || false;
                                    if (indicatorSpeaking && !prev) {
                                        window.logBot(`[Google Poll] SPEAKER_START ${getGoogleParticipantName(container)}`);
                                        sendGoogleSpeakerEvent('SPEAKER_START', container);
                                        lastSpeakingById.set(id, true);
                                        speakingStates.set(id, 'speaking');
                                    }
                                    else if (!indicatorSpeaking && prev) {
                                        window.logBot(`[Google Poll] SPEAKER_END ${getGoogleParticipantName(container)}`);
                                        sendGoogleSpeakerEvent('SPEAKER_END', container);
                                        lastSpeakingById.set(id, false);
                                        speakingStates.set(id, 'silent');
                                    }
                                    else if (!lastSpeakingById.has(id)) {
                                        lastSpeakingById.set(id, indicatorSpeaking);
                                    }
                                });
                            }, 500);
                        };
                        if (!degradedNoMedia && transcriptionEnabled && whisperLiveService) {
                            initializeGoogleSpeakerDetection(whisperLiveService, audioService, botConfigData);
                        }
                        // Participant counting: MutationObserver-based persistent registry.
                        // Fixes #189 (transient DOM flickers) and #190 (text scan noise).
                        window.logBot("Initializing participant registry (MutationObserver)...");
                        const _vexaKnownParticipants = new Set();
                        const _vexaLeaveTimers = new Map();
                        const LEAVE_GRACE_MS = 5000;
                        function _vexaSyncParticipants() {
                            const current = new Set(
                                Array.from(document.querySelectorAll('[data-participant-id]'))
                                    .map(el => el.getAttribute('data-participant-id'))
                                    .filter(Boolean)
                            );
                            for (const id of current) {
                                if (!_vexaKnownParticipants.has(id)) {
                                    _vexaKnownParticipants.add(id);
                                    window.logBot('[ParticipantRegistry] Joined: ' + id);
                                }
                                if (_vexaLeaveTimers.has(id)) {
                                    clearTimeout(_vexaLeaveTimers.get(id));
                                    _vexaLeaveTimers.delete(id);
                                }
                            }
                            for (const id of _vexaKnownParticipants) {
                                if (!current.has(id) && !_vexaLeaveTimers.has(id)) {
                                    _vexaLeaveTimers.set(id, setTimeout(() => {
                                        _vexaKnownParticipants.delete(id);
                                        _vexaLeaveTimers.delete(id);
                                        window.logBot('[ParticipantRegistry] Left: ' + id);
                                    }, LEAVE_GRACE_MS));
                                }
                            }
                        }
                        _vexaSyncParticipants();
                        const _vexaParticipantObserver = new MutationObserver(_vexaSyncParticipants);
                        _vexaParticipantObserver.observe(document.body, { childList: true, subtree: true });
                        window.getGoogleMeetActiveParticipants = () => {
                            const ids = Array.from(_vexaKnownParticipants);
                            window.logBot('🔍 [Google Meet Participants] ' + JSON.stringify(ids));
                            return ids;
                        };
                        window.getGoogleMeetActiveParticipantsCount = () => {
                            return _vexaKnownParticipants.size;
                        };
                        // Setup Google Meet meeting monitoring (browser context)
                        const setupGoogleMeetingMonitoring = (botConfigData, audioService, whisperLiveService, resolve) => {
                            window.logBot("Setting up Google Meet meeting monitoring...");
                            const leaveCfg = (botConfigData && botConfigData.automaticLeave) || {};
                            const startupAloneTimeoutSeconds = Number(leaveCfg.startupAloneTimeoutSeconds ?? (20 * 60));
                            const everyoneLeftTimeoutSeconds = leaveCfg.everyoneLeftTimeout
                                ? Math.floor(Number(leaveCfg.everyoneLeftTimeout) / 1000)
                                : Number(leaveCfg.everyoneLeftTimeoutSeconds ?? 60);
                            let aloneTime = 0;
                            let lastParticipantCount = 0;
                            let speakersIdentified = false;
                            let hasEverHadMultipleParticipants = false;
                            let monitoringStopped = false;
                            const stopWithFlush = async (reason, finish) => {
                                if (monitoringStopped)
                                    return;
                                monitoringStopped = true;
                                clearInterval(checkInterval);
                                try {
                                    if (typeof window.__vexaFlushRecordingBlob === "function") {
                                        await window.__vexaFlushRecordingBlob(reason);
                                    }
                                }
                                catch (flushErr) {
                                    window.logBot?.(`[Google Recording] Flush error during shutdown (${reason}): ${flushErr?.message || flushErr}`);
                                }
                                audioService.disconnect();
                                if (whisperLiveService) {
                                    whisperLiveService.close();
                                }
                                finish();
                            };
                            const checkInterval = setInterval(() => {
                                // Check participant count using the comprehensive helper
                                const currentParticipantCount = window.getGoogleMeetActiveParticipantsCount ? window.getGoogleMeetActiveParticipantsCount() : 0;
                                if (currentParticipantCount !== lastParticipantCount) {
                                    window.logBot(`Participant check: Found ${currentParticipantCount} unique participants from central list.`);
                                    lastParticipantCount = currentParticipantCount;
                                    // Track if we've ever had multiple participants
                                    if (currentParticipantCount > 1) {
                                        hasEverHadMultipleParticipants = true;
                                        speakersIdentified = true; // Once we see multiple participants, we've identified speakers
                                        window.logBot("Speakers identified - switching to post-speaker monitoring mode");
                                    }
                                }
                                if (currentParticipantCount <= 1) {
                                    if (aloneTime === 0) {
                                        window.logBot(`[AloneTimer] Started counting - participant count dropped to ${currentParticipantCount}`);
                                    }
                                    aloneTime++;
                                    // Determine timeout based on whether speakers have been identified
                                    const currentTimeout = speakersIdentified ? everyoneLeftTimeoutSeconds : startupAloneTimeoutSeconds;
                                    const timeoutDescription = speakersIdentified ? "post-speaker" : "startup";
                                    if (aloneTime >= currentTimeout) {
                                        if (speakersIdentified) {
                                            window.logBot(`Google Meet meeting ended or bot has been alone for ${everyoneLeftTimeoutSeconds} seconds after speakers were identified. Stopping recorder...`);
                                            void stopWithFlush("left_alone_timeout", () => reject(new Error("GOOGLE_MEET_BOT_LEFT_ALONE_TIMEOUT")));
                                        }
                                        else {
                                            window.logBot(`Google Meet bot has been alone for ${startupAloneTimeoutSeconds / 60} minutes during startup with no other participants. Stopping recorder...`);
                                            void stopWithFlush("startup_alone_timeout", () => reject(new Error("GOOGLE_MEET_BOT_STARTUP_ALONE_TIMEOUT")));
                                        }
                                    }
                                    else if (aloneTime > 0 && aloneTime % 10 === 0) { // Log every 10 seconds to avoid spam
                                        if (speakersIdentified) {
                                            window.logBot(`Bot has been alone for ${aloneTime} seconds (${timeoutDescription} mode). Will leave in ${currentTimeout - aloneTime} more seconds.`);
                                        }
                                        else {
                                            const remainingMinutes = Math.floor((currentTimeout - aloneTime) / 60);
                                            const remainingSeconds = (currentTimeout - aloneTime) % 60;
                                            window.logBot(`Bot has been alone for ${aloneTime} seconds during startup. Will leave in ${remainingMinutes}m ${remainingSeconds}s.`);
                                        }
                                    }
                                }
                                else {
                                    aloneTime = 0; // Reset if others are present
                                    if (hasEverHadMultipleParticipants && !speakersIdentified) {
                                        speakersIdentified = true;
                                        window.logBot("Speakers identified - switching to post-speaker monitoring mode");
                                    }
                                }
                            }, 1000);
                            // Listen for page unload
                            window.addEventListener("beforeunload", () => {
                                window.logBot("Page is unloading. Stopping recorder...");
                                void stopWithFlush("beforeunload", () => resolve());
                            });
                            document.addEventListener("visibilitychange", () => {
                                if (document.visibilityState === "hidden") {
                                    window.logBot("Document is hidden. Stopping recorder...");
                                    void stopWithFlush("visibility_hidden", () => resolve());
                                }
                            });
                        };
                        setupGoogleMeetingMonitoring(botConfigData, audioService, whisperLiveService, resolve);
                    }).catch((err) => {
                        reject(err);
                    });
                })(); // Close async IIFE
            }
            catch (error) {
                return reject(new Error("[Google Meet BOT Error] " + error.message));
            }
        });
        // Define reconfiguration hook to update language/task and reconnect
        window.triggerWebSocketReconfigure = async (lang, task) => {
            try {
                const svc = window.__vexaWhisperLiveService;
                const cfg = window.__vexaBotConfig || {};
                if (!svc) {
                    window.logBot?.('[Reconfigure] WhisperLive service not initialized.');
                    return;
                }
                cfg.language = lang;
                cfg.task = task || 'transcribe';
                window.__vexaBotConfig = cfg;
                try {
                    svc.close();
                }
                catch { }
                await svc.connectToWhisperLive(cfg, window.__vexaOnMessage, window.__vexaOnError, window.__vexaOnClose);
                window.logBot?.(`[Reconfigure] Applied: language=${cfg.language}, task=${cfg.task}`);
            }
            catch (e) {
                window.logBot?.(`[Reconfigure] Error applying new config: ${e?.message || e}`);
            }
        };
    }, {
        botConfigData: botConfig,
        whisperUrlForBrowser: whisperLiveUrl,
        selectors: {
            participantSelectors: selectors_1.googleParticipantSelectors,
            speakingClasses: selectors_1.googleSpeakingClassNames,
            silenceClasses: selectors_1.googleSilenceClassNames,
            containerSelectors: selectors_1.googleParticipantContainerSelectors,
            nameSelectors: selectors_1.googleNameSelectors,
            speakingIndicators: selectors_1.googleSpeakingIndicators,
            peopleButtonSelectors: selectors_1.googlePeopleButtonSelectors
        }
    });
    // After page.evaluate finishes, cleanup services
    if (whisperLiveService) {
        await whisperLiveService.cleanup();
    }
}
//# sourceMappingURL=recording.js.map