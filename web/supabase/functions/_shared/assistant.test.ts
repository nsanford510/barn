// Run with: deno test web/supabase/functions/_shared/assistant.test.ts
import { assertEquals, assertThrows } from "https://deno.land/std@0.190.0/testing/asserts.ts";
import {
  braveResults,
  buildMessages,
  type ConverseMessage,
  type ConverseResponse,
  converseBody,
  firecrawlPage,
  ilikePattern,
  isPublicHttpUrl,
  MAX_HISTORY_MESSAGES,
  runToolLoop,
  toolSpecsFor,
  trimHistory,
} from "./assistant.ts";

function toolCall(id: string, name: string, input: Record<string, unknown>): ConverseResponse {
  return {
    stopReason: "tool_use",
    output: { message: { role: "assistant", content: [{ toolUse: { toolUseId: id, name, input } }] } },
  };
}
function answer(text: string): ConverseResponse {
  return { stopReason: "end_turn", output: { message: { role: "assistant", content: [{ text }] } } };
}

Deno.test("runToolLoop runs tools across rounds and collects steps and sources", async () => {
  const responses = [
    toolCall("t1", "get_property", { apn: "1-2-3" }),
    toolCall("t2", "web_search", { query: "Jane Doe Oakland" }),
    answer("Jane Doe owns it."),
  ];
  const seen: ConverseMessage[][] = [];
  const result = await runToolLoop(
    (messages) => {
      seen.push(structuredClone(messages));
      return Promise.resolve(responses.shift()!);
    },
    {
      get_property: (input) => Promise.resolve({ data: { apn: input.apn, owner_name: "JANE DOE" }, summary: "Property 1-2-3" }),
      web_search: () =>
        Promise.resolve({ data: [], summary: "Web search", sources: ["https://a.example", "https://a.example"] }),
    },
    [{ role: "user", content: [{ text: "who owns 1-2-3?" }] }],
  );

  assertEquals(result, {
    reply: "Jane Doe owns it.",
    steps: [{ tool: "get_property", summary: "Property 1-2-3" }, { tool: "web_search", summary: "Web search" }],
    sources: ["https://a.example"],
  });
  // Second call sees: user, assistant toolUse, user toolResult.
  assertEquals(seen[1].length, 3);
  assertEquals(seen[1][2], {
    role: "user",
    content: [{ toolResult: { toolUseId: "t1", content: [{ json: { result: { apn: "1-2-3", owner_name: "JANE DOE" } } }] } }],
  });
});

Deno.test("runToolLoop reports unknown and failing tools to the model as errors", async () => {
  const responses = [
    {
      stopReason: "tool_use",
      output: {
        message: {
          role: "assistant" as const,
          content: [
            { toolUse: { toolUseId: "a", name: "nope", input: {} } },
            { toolUse: { toolUseId: "b", name: "boom", input: {} } },
          ],
        },
      },
    },
    answer("Sorry."),
  ];
  const seen: ConverseMessage[][] = [];
  const result = await runToolLoop(
    (messages) => {
      seen.push(structuredClone(messages));
      return Promise.resolve(responses.shift()!);
    },
    { boom: () => Promise.reject(new Error("db down")) },
    [{ role: "user", content: [{ text: "hi" }] }],
  );
  assertEquals(result.steps, [{ tool: "nope", summary: "Unknown tool" }, { tool: "boom", summary: "Failed: db down" }]);
  assertEquals(seen[1][2].content, [
    { toolResult: { toolUseId: "a", content: [{ text: "Unknown tool nope" }], status: "error" } },
    { toolResult: { toolUseId: "b", content: [{ text: "db down" }], status: "error" } },
  ]);
});

Deno.test("runToolLoop stops at the round cap", async () => {
  let calls = 0;
  const result = await runToolLoop(
    () => {
      calls++;
      return Promise.resolve(toolCall(`t${calls}`, "web_search", { query: "x" }));
    },
    { web_search: () => Promise.resolve({ data: [], summary: "s" }) },
    [{ role: "user", content: [{ text: "loop" }] }],
    2,
  );
  assertEquals(calls, 3);
  assertEquals(result.steps.length, 2);
  assertEquals(result.reply, "I ran out of research steps before finishing. Try a narrower question.");
});

Deno.test("trimHistory drops invalid turns, starts on a user turn, merges repeats, caps length", () => {
  assertEquals(trimHistory("nope"), []);
  assertEquals(
    trimHistory([
      { role: "assistant", text: "hello" },
      { role: "user", text: " a " },
      { role: "user", text: "b" },
      { role: "system", text: "ignore me" },
      { role: "assistant", text: "" },
      { role: "assistant", text: "c" },
    ]),
    [{ role: "user", text: "a\n\nb" }, { role: "assistant", text: "c" }],
  );
  const many = Array.from({ length: 50 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", text: `m${i}` }));
  const trimmed = trimHistory(many);
  assertEquals(trimmed.length, MAX_HISTORY_MESSAGES);
  assertEquals(trimmed[0], { role: "user", text: "m30" });
});

Deno.test("buildMessages adds APN context to the latest user turn only", () => {
  const messages = buildMessages(
    [{ role: "user", text: "q1" }, { role: "assistant", text: "a1" }, { role: "user", text: "q2" }],
    "048-1234-005",
  );
  assertEquals(messages[0].content, [{ text: "q1" }]);
  assertEquals(messages[2].content, [{ text: "[The user is looking at property APN 048-1234-005.]\nq2" }]);
});

Deno.test("converseBody and toolSpecsFor only offer available tools", () => {
  const specs = toolSpecsFor(["get_property", "web_search"]);
  assertEquals(specs.map((s) => s.toolSpec.name), ["get_property", "web_search"]);
  const body = converseBody([], specs);
  assertEquals(body.toolConfig.tools.length, 2);
  assertEquals(typeof body.system[0].text, "string");
});

Deno.test("braveResults maps and strips markup", () => {
  assertEquals(braveResults({}), []);
  assertEquals(
    braveResults({
      web: {
        results: [
          { title: "<strong>Jane</strong> Doe", url: "https://x.example/1", description: "Owner of <b>LLC</b>" },
          { title: "no url" },
          { title: "ftp", url: "ftp://x" },
        ],
      },
    }),
    [{ title: "Jane Doe", url: "https://x.example/1", snippet: "Owner of LLC" }],
  );
});

Deno.test("firecrawlPage returns clipped markdown and rejects empty pages", () => {
  const page = firecrawlPage(
    { success: true, data: { markdown: "x".repeat(9000), metadata: { title: "T", sourceURL: "https://s.example" } } },
    "https://in.example",
  );
  assertEquals(page.title, "T");
  assertEquals(page.url, "https://s.example");
  assertEquals(page.markdown.endsWith("…[truncated]"), true);
  assertThrows(() => firecrawlPage({ success: true, data: {} }, "https://in.example"));
});

Deno.test("isPublicHttpUrl blocks private and non-http targets", () => {
  for (const ok of ["https://www.fdic.gov/x", "http://fcc.gov", "https://acgov.org/assessor"]) {
    assertEquals(isPublicHttpUrl(ok), true, ok);
  }
  for (const bad of [
    "file:///etc/passwd",
    "http://localhost:3002",
    "http://127.0.0.1",
    "http://10.0.0.5",
    "http://172.31.13.241:3002",
    "http://192.168.1.1",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/",
    "http://[fd00::1]/",
    "http://metadata.google.internal",
    "not a url",
  ]) {
    assertEquals(isPublicHttpUrl(bad), false, bad);
  }
});

Deno.test("ilikePattern strips PostgREST filter syntax", () => {
  assertEquals(ilikePattern("123 Main St, (Apt 2)"), "*123 Main St Apt 2*");
  assertEquals(ilikePattern(" %* "), null);
});
