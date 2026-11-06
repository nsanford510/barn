import { test } from "node:test";
import assert from "node:assert/strict";
import { relayGeminiVoice } from "./gemini-voice.ts";
import { liveConfig } from "./gemini-live.ts";

class Socket {
  readyState = 1;
  sent: any[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: any }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send(raw: any) { this.sent.push(typeof raw === "string" ? JSON.parse(raw) : raw); }
  close() { this.readyState = 3; this.onclose?.(); }
  receive(body: unknown) { this.onmessage?.({ data: JSON.stringify(body) }); }
}
const config = liveConfig({ GOOGLE_AGENT_PLATFORM_API_KEY: "server-only-key", GOOGLE_AGENT_PLATFORM_PROJECT: "test-project" });
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));
async function session(tools = {}, limits = {}) {
  const phone = new Socket(), google = new Socket();
  const relay = relayGeminiVoice(phone as unknown as WebSocket, config, tools, {
    openSocket: () => google as unknown as WebSocket, maxMs: 2_000, ...limits,
  });
  phone.receive({ type: "start", messages: [{ role: "user", text: "Earlier question" }, { role: "assistant", text: "Earlier answer" }], apn: "123" });
  await tick(); google.onopen?.();
  google.receive({ setupComplete: {} });
  await tick();
  return { phone, google, relay };
}

test("voice setup keeps the Google key private and seeds history without a new response", async () => {
  const { phone, google, relay } = await session();
  try {
    assert.equal(phone.sent[0].type, "ready");
    assert.ok(!JSON.stringify(phone.sent).includes("server-only-key"));
    assert.match(google.sent[0].setup.systemInstruction.parts[0].text, /Current property APN: 123/);
    assert.deepEqual(google.sent[0].setup.inputAudioTranscription, {});
    assert.equal(google.sent[1].clientContent.turnComplete, false);
  } finally { relay.close(); }
});

test("streams 16 kHz microphone audio and ends the input on mic release", async () => {
  const { phone, google, relay } = await session();
  try {
    phone.onmessage?.({ data: new Uint8Array([0, 1, 2, 3]).buffer });
    phone.receive({ type: "mic", open: false });
    await tick();
    assert.equal(google.sent[2].realtimeInput.audio.mimeType, "audio/pcm;rate=16000");
    assert.equal(google.sent[2].realtimeInput.audio.data, "AAECAw==");
    assert.equal(google.sent[3].realtimeInput.audioStreamEnd, true);
  } finally { relay.close(); }
});

test("returns PCM audio, running transcripts and turn boundaries", async () => {
  const { phone, google, relay } = await session();
  try {
    google.receive({ serverContent: { inputTranscription: { text: "Who owns it?" } } });
    google.receive({ serverContent: { outputTranscription: { text: "Example LLC." }, modelTurn: { parts: [{ inlineData: { mimeType: "audio/pcm;rate=24000", data: "AAECAw==" } }] }, turnComplete: true } });
    await tick();
    assert.ok(phone.sent.some((frame) => frame.type === "user" && frame.text === "Who owns it?"));
    assert.ok(phone.sent.some((frame) => frame.type === "assistant" && frame.text === "Example LLC."));
    assert.ok(phone.sent.some((frame) => frame instanceof Uint8Array && frame.length === 4));
    assert.equal(phone.sent.at(-1).type, "turn");
  } finally { relay.close(); }
});

test("runs grounded research tools and sends sources to Android", async () => {
  const { phone, google, relay } = await session({ get_property: async (args: any) => ({ data: { apn: args.apn }, summary: "Found property", sources: ["https://county.example"] }) });
  try {
    google.receive({ toolCall: { functionCalls: [{ id: "1", name: "get_property", args: { apn: "123" } }] } });
    await tick();
    assert.equal(google.sent.at(-1).toolResponse.functionResponses[0].response.result.apn, "123");
    assert.ok(phone.sent.some((frame) => frame.type === "sources" && frame.urls[0] === "https://county.example"));
  } finally { relay.close(); }
});

test("unknown tools and tool failures do not reveal upstream credentials", async () => {
  const { phone, google, relay } = await session({ web_search: async () => { throw new Error("private-upstream-token"); } });
  try {
    google.receive({ toolCall: { functionCalls: [{ id: "1", name: "toString" }, { id: "2", name: "web_search" }] } });
    await tick();
    assert.equal(google.sent.at(-1).toolResponse.functionResponses.length, 2);
    assert.ok(!JSON.stringify(phone.sent).includes("private-upstream-token"));
    assert.ok(!JSON.stringify(google.sent).includes("private-upstream-token"));
  } finally { relay.close(); }
});

test("phone disconnect closes Google and upstream failures return safe errors", async () => {
  const a = await session();
  a.phone.close();
  assert.equal(a.google.readyState, 3);
  const b = await session();
  b.google.receive({ error: { message: "wss://example?key=secret" } });
  await tick();
  assert.equal(b.phone.readyState, 3);
  assert.equal(b.google.readyState, 3);
  assert.ok(!JSON.stringify(b.phone.sent).includes("key=secret"));
});

test("rejects oversized audio and closes sessions at their cap", async () => {
  const a = await session();
  a.phone.onmessage?.({ data: new Uint8Array(32_002).buffer });
  await tick();
  assert.equal(a.phone.readyState, 3);
  const b = await session({}, { maxMs: 20 });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(b.phone.readyState, 3);
  assert.equal(b.google.readyState, 3);
});
