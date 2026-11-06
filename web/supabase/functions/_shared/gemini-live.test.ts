import { test } from "node:test";
import assert from "node:assert/strict";
import { liveConfig, liveHistory, liveSetup, liveSocketUrl, runGeminiLiveTurn } from "./gemini-live.ts";

const config = liveConfig({ GOOGLE_AGENT_PLATFORM_API_KEY: "test-key", GOOGLE_AGENT_PLATFORM_PROJECT: "test-project" });
const history = [{ role: "user" as const, text: "Who owns this property?" }];

class FakeSocket {
  readyState = 1;
  sent: any[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string | Blob }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send(raw: string) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.onclose?.(); }
  receive(body: unknown) { this.onmessage?.({ data: JSON.stringify(body) }); }
}

function session(tools = {}, extra = {}) {
  const socket = new FakeSocket();
  const result = runGeminiLiveTurn(config, tools, history, "123", {
    openSocket: () => socket as unknown as WebSocket, timeoutMs: 1_000, ...extra,
  });
  socket.onopen?.();
  return { socket, result };
}
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

test("Claimtrace configuration is required, with the same regional Live model", () => {
  assert.equal(config.model, "gemini-3.8-live");
  assert.equal(config.location, "us-central1");
  assert.throws(() => liveConfig({}), /GOOGLE_AGENT_PLATFORM_PROJECT/);
  assert.throws(() => liveConfig({ GOOGLE_AGENT_PLATFORM_PROJECT: "project" }), /GOOGLE_AGENT_PLATFORM_API_KEY/);
  assert.throws(() => liveConfig({ GOOGLE_AGENT_PLATFORM_PROJECT: "../escape", GOOGLE_AGENT_PLATFORM_API_KEY: "key" }), /Invalid/);
  assert.match(liveSocketUrl(config), /^wss:\/\/us-central1-aiplatform.googleapis.com\//);
});

test("setup retains the research limits and only advertises available BARN tools", () => {
  const setup = liveSetup(config, { get_property: async () => ({ data: {}, summary: "ok" }) }).setup;
  assert.equal(setup.model, "projects/test-project/locations/us-central1/publishers/google/models/gemini-3.8-live");
  assert.deepEqual(setup.generationConfig.responseModalities, ["AUDIO"]);
  assert.match(setup.systemInstruction.parts[0].text, /Do not look up or reveal Social Security/);
  assert.deepEqual(setup.tools[0].functionDeclarations.map((x) => x.name), ["get_property"]);
  assert.equal(setup.tools[0].functionDeclarations[0].behavior, "BLOCKING");
});

test("history maps model roles and confines APN context to the newest question", () => {
  const body = liveHistory([
    { role: "user", text: "old" }, { role: "assistant", text: "answer" }, ...history,
  ], "123").clientContent;
  assert.equal(body.turns[1].role, "model");
  assert.equal(body.turns[0].parts[0].text, "old");
  assert.match(body.turns[2].parts[0].text, /APN 123/);
});

test("waits for setup, returns Gemini's transcript in the existing chat response", async () => {
  const { socket, result } = session();
  assert.equal(socket.sent.length, 1);
  socket.receive({ setupComplete: {} });
  await settle();
  assert.equal(socket.sent[1].clientContent.turnComplete, true);
  socket.receive({ serverContent: { outputTranscription: { text: "The owner is " } } });
  socket.receive({ serverContent: { outputTranscription: { text: "Example LLC." }, turnComplete: true } });
  assert.deepEqual(await result, { reply: "The owner is Example LLC.", steps: [], sources: [] });
  assert.equal(socket.readyState, 3);
});

test("executes BARN tools and returns tool data, steps and deduplicated sources", async () => {
  let args: unknown;
  const { socket, result } = session({ get_property: async (input: unknown) => {
    args = input;
    return { data: { owner: "Example LLC" }, summary: "Property 123", sources: ["https://county.example", "https://county.example"] };
  } });
  socket.receive({ toolCall: { functionCalls: [{ id: "call1", name: "get_property", args: { apn: "123" } }] }, serverContent: { turnComplete: true } });
  await settle();
  assert.deepEqual(args, { apn: "123" });
  assert.deepEqual(socket.sent.at(-1).toolResponse.functionResponses[0], { id: "call1", name: "get_property", response: { result: { owner: "Example LLC" } } });
  socket.receive({ serverContent: { outputTranscription: { text: "Example LLC, per BARN." }, turnComplete: true } });
  assert.deepEqual(await result, { reply: "Example LLC, per BARN.", steps: [{ tool: "get_property", summary: "Property 123" }], sources: ["https://county.example"] });
});

test("rejects unknown tools, including inherited object methods", async () => {
  const { socket, result } = session();
  socket.receive({ toolCall: { functionCalls: [{ id: "bad", name: "toString" }] } });
  await settle();
  assert.equal(socket.sent.at(-1).toolResponse.functionResponses[0].response.error, "Unknown tool");
  socket.receive({ serverContent: { outputTranscription: { text: "Unavailable." }, turnComplete: true } });
  assert.equal((await result).steps[0].summary, "Unknown tool");
});

test("tool failures never expose upstream secrets", async () => {
  const { socket, result } = session({ web_search: async () => { throw new Error("secret-key-in-upstream-url"); } });
  socket.receive({ toolCall: { functionCalls: [{ id: "bad", name: "web_search" }] } });
  await settle();
  assert.ok(!JSON.stringify(socket.sent).includes("secret-key-in-upstream-url"));
  socket.receive({ serverContent: { outputTranscription: { text: "The search is unavailable." }, turnComplete: true } });
  assert.equal((await result).steps[0].summary, "Tool unavailable");
});

test("cancelled tool results are not sent to Gemini", async () => {
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  const { socket, result } = session({ web_search: async () => { await waiting; return { data: {}, summary: "result" }; } });
  socket.receive({ toolCall: { functionCalls: [{ id: "cancel", name: "web_search" }] } });
  socket.receive({ toolCallCancellation: { ids: ["cancel"] } });
  await settle();
  release();
  await settle();
  assert.equal(socket.sent.length, 1);
  socket.receive({ serverContent: { outputTranscription: { text: "Cancelled." }, turnComplete: true } });
  assert.deepEqual((await result).steps, []);
});

test("caps research calls and closes the upstream session", async () => {
  let calls = 0;
  const { socket, result } = session({ get_property: async () => { calls++; return { data: {}, summary: "ok" }; } });
  socket.receive({ toolCall: { functionCalls: Array.from({ length: 7 }, (_, i) => ({ id: String(i), name: "get_property" })) } });
  assert.match((await result).reply, /ran out of research steps/);
  assert.equal(calls, 6);
  assert.equal(socket.readyState, 3);
});

test("upstream errors fail safely without leaking the Google URL", async () => {
  const { socket, result } = session();
  const check = assert.rejects(result, /^Error: Gemini Live request failed$/);
  socket.receive({ error: { message: "wss://example?key=secret" } });
  await check;
  assert.equal(socket.readyState, 3);
});

test("close before the answer fails instead of returning partial text", async () => {
  const { socket, result } = session();
  const check = assert.rejects(result, /closed before completing/);
  socket.receive({ serverContent: { outputTranscription: { text: "Partial" } } });
  socket.close();
  await check;
});

test("timeout and caller cancellation close the upstream connection", async () => {
  const timeout = session({}, { timeoutMs: 5 });
  await assert.rejects(timeout.result, /timed out/);
  assert.equal(timeout.socket.readyState, 3);
  const controller = new AbortController();
  const cancelled = session({}, { signal: controller.signal });
  const check = assert.rejects(cancelled.result, /cancelled/);
  controller.abort();
  await check;
  assert.equal(cancelled.socket.readyState, 3);
});
