// Authenticated BARN-to-Gemini audio relay. PCM16 in: 16 kHz mono; out: 24 kHz mono.
import { type LiveConfig, liveHistory, liveSetup, liveSocketUrl } from "./gemini-live.ts";
import { type ChatTurn, SYSTEM_PROMPT, trimHistory, type ToolFn } from "./assistant.ts";

export function relayGeminiVoice(
  phone: WebSocket,
  config: LiveConfig,
  tools: Record<string, ToolFn>,
  options: { openSocket?: (url: string) => WebSocket; maxMs?: number; idleMs?: number } = {},
) {
  let google: WebSocket | null = null;
  let finished = false;
  let ready = false;
  let started = false;
  let userText = "";
  let assistantText = "";
  let lastActivity = Date.now();
  let pending = 0;
  let callsThisTurn = 0;
  let incoming = Promise.resolve();
  let phoneIncoming = Promise.resolve();
  const cancelled = new Set<string>();
  const send = (frame: unknown) => { if (!finished && phone.readyState === 1) phone.send(JSON.stringify(frame)); };
  const upstream = (frame: unknown) => { if (!finished && google?.readyState === 1) google.send(JSON.stringify(frame)); };
  const finish = (message?: string) => {
    if (finished) return;
    send({ type: "ended", ...(message ? { message } : {}) });
    finished = true;
    clearTimeout(cap); clearTimeout(handshake); clearInterval(idle);
    if (google && google.readyState < 2) google.close(1000);
    if (phone.readyState < 2) phone.close(1000);
  };
  const fail = (message: string) => { send({ type: "error", message }); finish(); };
  const cap = setTimeout(() => finish("Voice session ended. Reconnect to continue."), options.maxMs ?? 8 * 60_000);
  const handshake = setTimeout(() => { if (!ready) fail("Voice could not connect. Try again."); }, 20_000);
  const idleMs = options.idleMs ?? 90_000;
  const idle = setInterval(() => {
    if (!pending && Date.now() - lastActivity > idleMs) finish("Voice paused after inactivity. Reconnect to continue.");
  }, Math.min(idleMs, 5_000));

  phone.onclose = () => finish();
  phone.onerror = () => finish();
  phone.onmessage = (event) => {
    phoneIncoming = phoneIncoming.then(() => fromPhone(event.data)).catch(() => fail("Invalid voice message."));
  };

  async function fromPhone(data: unknown) {
    if (finished) return;
    if (data instanceof Blob || data instanceof ArrayBuffer) {
      if (!ready) return;
      const bytes = new Uint8Array(data instanceof Blob ? await data.arrayBuffer() : data);
      if (!bytes.length || bytes.length > 32_000 || bytes.length % 2) { fail("Invalid microphone audio."); return; }
      let binary = "";
      for (const byte of bytes) binary += String.fromCharCode(byte);
      lastActivity = Date.now();
      upstream({ realtimeInput: { audio: { mimeType: "audio/pcm;rate=16000", data: btoa(binary) } } });
      return;
    }
    if (typeof data !== "string" || data.length > 100_000) { fail("Invalid voice message."); return; }
    const frame = JSON.parse(data);
    if (frame.type === "stop") { finish(); return; }
    if (frame.type === "start" && !started) {
      started = true;
      const history = trimHistory(frame.messages);
      const apn = typeof frame.apn === "string" ? frame.apn.trim().slice(0, 64) : "";
      const setup = liveSetup(config, tools);
      setup.setup.systemInstruction.parts[0].text = SYSTEM_PROMPT +
        "\nYou are speaking live with the user. Use short spoken sentences, no markdown or tables. Stop speaking when interrupted." +
        (apn ? `\nCurrent property APN: ${apn}.` : "");
      google = (options.openSocket ?? ((url) => new WebSocket(url)))(liveSocketUrl(config));
      google.onopen = () => upstream(setup);
      google.onerror = () => fail("Gemini voice connection failed. Try again.");
      google.onclose = () => finish("Gemini voice connection ended. Reconnect to continue.");
      google.onmessage = (event) => {
        incoming = incoming.then(() => fromGoogle(event.data, history)).catch(() => fail("Gemini voice returned an invalid response."));
      };
      return;
    }
    if (!ready) return;
    lastActivity = Date.now();
    if (frame.type === "mic" && frame.open === false) upstream({ realtimeInput: { audioStreamEnd: true } });
    if (frame.type === "text" && typeof frame.text === "string" && frame.text.trim()) {
      userText = frame.text.trim().slice(0, 4_000);
      send({ type: "user", text: userText });
      upstream({ realtimeInput: { text: userText } });
    }
  }

  async function fromGoogle(data: unknown, history: ChatTurn[]) {
    if (finished) return;
    const raw = data instanceof Blob ? await data.text() : data;
    if (typeof raw !== "string") throw new Error("Expected JSON");
    const message = JSON.parse(raw);
    if (message.error) { fail("Gemini voice is unavailable. Check the server model configuration."); return; }
    if (message.setupComplete && !ready) {
      ready = true;
      clearTimeout(handshake);
      if (history.length) {
        const context = liveHistory(history).clientContent;
        // Seed conversation history without asking the model to answer the last historical turn again.
        upstream({ clientContent: { ...context, turnComplete: false } });
      }
      send({ type: "ready" });
    }
    for (const id of message.toolCallCancellation?.ids ?? []) cancelled.add(id);
    const content = message.serverContent;
    if (content) {
      lastActivity = Date.now();
      if (content.interrupted) {
        send({ type: "interrupted" });
        userText = ""; assistantText = "";
      }
      for (const part of content.modelTurn?.parts ?? []) {
        if (part.inlineData?.mimeType?.startsWith("audio/pcm") && typeof part.inlineData.data === "string") {
          const raw = atob(part.inlineData.data);
          if (phone.readyState === 1) phone.send(Uint8Array.from(raw, (char) => char.charCodeAt(0)));
        }
      }
      if (typeof content.inputTranscription?.text === "string") {
        userText += content.inputTranscription.text;
        send({ type: "user", text: userText });
      }
      if (typeof content.outputTranscription?.text === "string") {
        assistantText += content.outputTranscription.text;
        send({ type: "assistant", text: assistantText });
      }
      if (content.turnComplete && !pending && assistantText.trim() && !message.toolCall?.functionCalls?.length) {
        send({ type: "turn" });
        userText = ""; assistantText = ""; callsThisTurn = 0; cancelled.clear();
      }
    }
    const calls = message.toolCall?.functionCalls;
    if (Array.isArray(calls) && calls.length) {
      pending += calls.length;
      void execute(calls).catch(() => fail("Voice research could not complete."));
    }
    if (message.goAway) finish("Voice session is ending. Reconnect to continue.");
  }

  async function execute(calls: Array<{ id: string; name: string; args?: Record<string, unknown> }>) {
    const responses = [];
    for (const call of calls) {
      try {
        if (finished || cancelled.has(call.id)) continue;
        if (++callsThisTurn > 6) { fail("Research limit reached. Reconnect and ask a narrower question."); return; }
        if (!Object.hasOwn(tools, call.name)) {
          responses.push({ id: call.id, name: call.name, response: { error: "Unknown tool" } });
          continue;
        }
        send({ type: "activity", tool: call.name });
        const args = call.args && typeof call.args === "object" && !Array.isArray(call.args) ? call.args : {};
        const result = await tools[call.name](args);
        if (finished || cancelled.has(call.id)) continue;
        if (result.sources?.length) send({ type: "sources", urls: result.sources });
        responses.push({ id: call.id, name: call.name, response: { result: result.data } });
      } catch {
        if (!finished && !cancelled.has(call.id)) responses.push({ id: call.id, name: call.name, response: { error: "Tool unavailable. Do not guess its results." } });
      } finally { pending--; lastActivity = Date.now(); }
    }
    if (responses.length) upstream({ toolResponse: { functionResponses: responses } });
  }
  return { close: finish };
}
