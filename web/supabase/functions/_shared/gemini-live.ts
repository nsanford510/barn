// Gemini Live stays on the server. The Android chat contract remains { reply, steps, sources }.
import {
  type AssistantReply,
  type ChatTurn,
  MAX_TOOL_ROUNDS,
  SYSTEM_PROMPT,
  type ToolFn,
  toolSpecsFor,
} from "./assistant.ts";

export const DEFAULT_LIVE_MODEL = "gemini-3.8-live";
export const DEFAULT_LIVE_LOCATION = "us-central1";

export type LiveConfig = { apiKey: string; project: string; location: string; model: string };

// Uses the same server-side configuration names as Claimtrace's Alex voice relay.
export function liveConfig(env: Record<string, string | undefined>): LiveConfig {
  const need = (name: string) => {
    const value = env[name]?.trim();
    if (!value) throw new Error(`Missing ${name}`);
    return value;
  };
  const project = need("GOOGLE_AGENT_PLATFORM_PROJECT");
  const location = env.ASSISTANT_GEMINI_LOCATION?.trim() || DEFAULT_LIVE_LOCATION;
  const model = env.ASSISTANT_GEMINI_MODEL?.trim() || DEFAULT_LIVE_MODEL;
  if (!/^[a-zA-Z0-9_-]+$/.test(project) || !/^[a-z0-9-]+$/.test(location) || !/^[a-zA-Z0-9._-]+$/.test(model)) {
    throw new Error("Invalid Gemini project, location or model");
  }
  return { apiKey: need("GOOGLE_AGENT_PLATFORM_API_KEY"), project, location, model };
}

export function liveSocketUrl(config: LiveConfig): string {
  const host = config.location === "global" ? "aiplatform.googleapis.com" : `${config.location}-aiplatform.googleapis.com`;
  return `wss://${host}/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent?key=${encodeURIComponent(config.apiKey)}`;
}

export function liveSetup(config: LiveConfig, tools: Record<string, ToolFn>) {
  return {
    setup: {
      model: `projects/${config.project}/locations/${config.location}/publishers/google/models/${config.model}`,
      generationConfig: { responseModalities: ["AUDIO"] },
      // Live's audio response is transcribed by Gemini; return that text through the existing chat API.
      outputAudioTranscription: {},
      inputAudioTranscription: {},
      systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
      tools: [{ functionDeclarations: toolSpecsFor(Object.keys(tools)).map(({ toolSpec }) => ({
        name: toolSpec.name,
        description: toolSpec.description,
        parametersJsonSchema: toolSpec.inputSchema.json,
        behavior: "BLOCKING",
      })) }],
    },
  };
}

export function liveHistory(history: ChatTurn[], apn?: string | null) {
  return {
    clientContent: {
      turns: history.map((turn, index) => ({
        role: turn.role === "assistant" ? "model" : "user",
        parts: [{ text: (index === history.length - 1 && apn ? `[The user is looking at property APN ${apn}.]\n` : "") + turn.text }],
      })),
      turnComplete: true,
    },
  };
}

type FunctionCall = { id: string; name: string; args?: Record<string, unknown> };
type LiveMessage = {
  setupComplete?: unknown;
  error?: unknown;
  toolCall?: { functionCalls?: FunctionCall[] };
  toolCallCancellation?: { ids?: string[] };
  serverContent?: {
    outputTranscription?: { text?: string };
    turnComplete?: boolean;
    interrupted?: boolean;
  };
};

/** One existing Android chat/voice turn, using Gemini Live and BARN's existing tools. */
export function runGeminiLiveTurn(
  config: LiveConfig,
  tools: Record<string, ToolFn>,
  history: ChatTurn[],
  apn?: string | null,
  options: { openSocket?: (url: string) => WebSocket; timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<AssistantReply> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) { reject(new Error("Assistant request cancelled")); return; }
    const socket = (options.openSocket ?? ((url) => new WebSocket(url)))(liveSocketUrl(config));
    let done = false;
    let ready = false;
    let reply = "";
    let calls = 0;
    let pending = 0;
    let answerSinceTool = false;
    let incoming = Promise.resolve();
    const cancelled = new Set<string>();
    const steps: AssistantReply["steps"] = [];
    const sources = new Set<string>();
    const send = (body: unknown) => { if (!done && socket.readyState === 1) socket.send(JSON.stringify(body)); };
    const finish = (error?: Error) => {
      if (done) return;
      done = true;
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
      // Never expose the upstream URL or close reason: it includes a server credential.
      if (socket.readyState < 2) socket.close(1000);
      if (error) reject(error);
      else resolve({ reply: reply.trim() || "I couldn't come up with an answer. Try rephrasing.", steps, sources: [...sources] });
    };
    const abort = () => finish(new Error("Assistant request cancelled"));
    const timeout = setTimeout(() => finish(new Error("Gemini Live timed out")), options.timeoutMs ?? 110_000);
    options.signal?.addEventListener("abort", abort, { once: true });
    socket.onopen = () => send(liveSetup(config, tools));
    socket.onerror = () => finish(new Error("Gemini Live connection failed"));
    socket.onclose = () => finish(new Error("Gemini Live closed before completing the answer"));
    socket.onmessage = (event) => {
      incoming = incoming.then(() => receive(event.data))
        .catch(() => finish(new Error("Invalid Gemini Live response")));
    };

    async function receive(data: unknown) {
      if (done) return;
      const raw = data instanceof Blob ? await data.text() : data;
      if (typeof raw !== "string") throw new Error("Expected JSON");
      const message = JSON.parse(raw) as LiveMessage;
      if (message.error) { finish(new Error("Gemini Live request failed")); return; }
      if (message.setupComplete && !ready) {
        ready = true;
        send(liveHistory(history, apn));
      }
      for (const id of message.toolCallCancellation?.ids ?? []) cancelled.add(id);
      const requested = message.toolCall?.functionCalls ?? [];
      if (requested.length) {
        answerSinceTool = false;
        pending += requested.length;
        void execute(requested).catch(() => finish(new Error("Gemini Live tool response failed")));
      }
      const content = message.serverContent;
      if (typeof content?.outputTranscription?.text === "string") {
        reply += content.outputTranscription.text;
        if (!pending) answerSinceTool = true;
      }
      if (content?.interrupted) { finish(new Error("Gemini Live answer was interrupted")); return; }
      // Tool calls can produce an intermediate turnComplete; wait for the grounded spoken answer.
      if (content?.turnComplete && reply.trim() && answerSinceTool && !pending && !requested.length) finish();
    }

    async function execute(requested: FunctionCall[]) {
      const responses = [];
      for (const call of requested) {
        try {
          if (done || cancelled.has(call.id)) continue;
          if (++calls > MAX_TOOL_ROUNDS) {
            reply = "I ran out of research steps before finishing. Try a narrower question.";
            finish();
            return;
          }
          const tool = Object.hasOwn(tools, call.name) ? tools[call.name] : undefined;
          if (!tool) {
            steps.push({ tool: call.name, summary: "Unknown tool" });
            responses.push({ id: call.id, name: call.name, response: { error: "Unknown tool" } });
            continue;
          }
          const args = call.args && typeof call.args === "object" && !Array.isArray(call.args) ? call.args : {};
          const result = await tool(args);
          if (done || cancelled.has(call.id)) continue;
          steps.push({ tool: call.name, summary: result.summary });
          result.sources?.forEach((url) => sources.add(url));
          responses.push({ id: call.id, name: call.name, response: { result: result.data } });
        } catch {
          // Tool fetch errors may include upstream response bodies. Keep credentials out of the reply.
          if (!done && !cancelled.has(call.id)) {
            steps.push({ tool: call.name, summary: "Tool unavailable" });
            responses.push({ id: call.id, name: call.name, response: { error: "Tool unavailable. Do not guess its results." } });
          }
        } finally {
          pending -= 1;
        }
      }
      if (responses.length) send({ toolResponse: { functionResponses: responses } });
    }
  });
}
