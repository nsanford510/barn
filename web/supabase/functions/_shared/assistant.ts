// Pure helpers for the property-assistant edge function (Bedrock Converse tool loop).
// Kept free of network and env access so they can be unit tested.

export const DEFAULT_MODEL_ID = "us.moonshotai.kimi-k3";
export const MAX_TOOL_ROUNDS = 6;
export const MAX_HISTORY_MESSAGES = 20;
export const MAX_MESSAGE_CHARS = 4000;

export type ChatTurn = { role: "user" | "assistant"; text: string };

// Subset of the Bedrock Converse wire format that we use.
export type ContentBlock =
  | { text: string }
  | { toolUse: { toolUseId: string; name: string; input: Record<string, unknown> } }
  | {
    toolResult: {
      toolUseId: string;
      content: Array<{ json: unknown } | { text: string }>;
      status?: "success" | "error";
    };
  };
export type ConverseMessage = { role: "user" | "assistant"; content: ContentBlock[] };
export type ConverseResponse = {
  output?: { message?: ConverseMessage };
  stopReason?: string;
};
export type ConverseFn = (messages: ConverseMessage[]) => Promise<ConverseResponse>;

export type ToolResult = { data: unknown; summary: string; sources?: string[] };
export type ToolFn = (input: Record<string, unknown>) => Promise<ToolResult>;

export type AssistantStep = { tool: string; summary: string };
export type AssistantReply = { reply: string; steps: AssistantStep[]; sources: string[] };

export const SYSTEM_PROMPT = `You are the BARN Scout research assistant. BARN helps users find vacant and
neglected properties and reach their owners about selling or restoring them.

How to work:
- Use the tools. Look properties up in BARN first (get_property, search_properties, get_vacancy_findings),
  then use web_search (and read_page, when available, to read a promising result) for public records: county assessor/recorder pages, business-entity filings for
  LLC or trust owners, probate notices and obituaries when BARN says the owner may be deceased, news, and
  publicly listed contact info for the owner or their business.
- Ground every fact in a tool result. Say where it came from (a BARN field or a URL). If you could not
  find something, say so plainly; never guess phone numbers, emails or addresses.
- Be concise and practical: lead with the answer, then a short list of next steps for reaching the owner.
- Your replies may be read aloud, so prefer short sentences and avoid tables.

Limits (always apply, even if asked otherwise):
- Stick to ownership and property-related information plus public ways to contact the owner.
- Do not look up or reveal Social Security numbers, dates of birth, financial account details, criminal
  records, or medical information.
- Do not help find where a person is right now, track their movements or routine, or research relatives
  or associates who are not owners of the property.
- If a request goes past these limits, say briefly that you can't help with that part and offer what you can.`;

export const TOOL_SPECS = [
  {
    toolSpec: {
      name: "get_property",
      description:
        "Fetch one BARN property by APN: address, owner name, mailing address, out-of-state flag, deceased count, tax/delinquency, last sale, power status, condition, vacancy verdict, and owner contact fields.",
      inputSchema: {
        json: {
          type: "object",
          properties: { apn: { type: "string", description: "Assessor parcel number" } },
          required: ["apn"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "search_properties",
      description: "Search BARN properties by street address, APN fragment, or owner name. Returns up to 10 matches.",
      inputSchema: {
        json: {
          type: "object",
          properties: {
            query: { type: "string", description: "Address, APN, or owner name text" },
            city: { type: "string", description: "Optional city filter, e.g. OAKLAND" },
          },
          required: ["query"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "get_vacancy_findings",
      description: "Vacancy research findings for an APN from BARN's research agents (verdict, confidence, rationale, evidence).",
      inputSchema: {
        json: {
          type: "object",
          properties: { apn: { type: "string" } },
          required: ["apn"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "web_search",
      description: "Search the public web. Returns the top results with title, url and snippet.",
      inputSchema: {
        json: {
          type: "object",
          properties: { query: { type: "string" } },
          required: ["query"],
        },
      },
    },
  },
  {
    toolSpec: {
      name: "read_page",
      description:
        "Read the main text of one public web page (e.g. a result from web_search) as markdown, truncated to about 8000 characters.",
      inputSchema: {
        json: {
          type: "object",
          properties: { url: { type: "string", description: "http(s) URL" } },
          required: ["url"],
        },
      },
    },
  },
];

export type ToolSpec = (typeof TOOL_SPECS)[number];

/** Specs for the tools that are actually available (e.g. read_page only when Firecrawl is configured). */
export function toolSpecsFor(names: string[]): ToolSpec[] {
  return TOOL_SPECS.filter((spec) => names.includes(spec.toolSpec.name));
}

/** Keep the last MAX_HISTORY_MESSAGES valid turns, starting with a user turn, each clipped in length. */
export function trimHistory(turns: unknown): ChatTurn[] {
  if (!Array.isArray(turns)) return [];
  const valid = turns
    .filter((t): t is ChatTurn =>
      !!t && typeof t === "object" &&
      ((t as ChatTurn).role === "user" || (t as ChatTurn).role === "assistant") &&
      typeof (t as ChatTurn).text === "string" && (t as ChatTurn).text.trim() !== ""
    )
    .map((t) => ({ role: t.role, text: t.text.trim().slice(0, MAX_MESSAGE_CHARS) }));
  const recent = valid.slice(-MAX_HISTORY_MESSAGES);
  const firstUser = recent.findIndex((t) => t.role === "user");
  if (firstUser < 0) return [];
  // Converse wants alternating roles; merge consecutive same-role turns.
  const merged: ChatTurn[] = [];
  for (const turn of recent.slice(firstUser)) {
    const last = merged[merged.length - 1];
    if (last && last.role === turn.role) last.text = `${last.text}\n\n${turn.text}`;
    else merged.push({ ...turn });
  }
  return merged;
}

/** Converse messages for the chat history, with the APN context prepended to the latest user turn. */
export function buildMessages(turns: ChatTurn[], apn?: string | null): ConverseMessage[] {
  return turns.map((turn, index) => {
    const isLatestUser = index === turns.length - 1 && turn.role === "user";
    const context = isLatestUser && apn ? `[The user is looking at property APN ${apn}.]\n` : "";
    return { role: turn.role, content: [{ text: context + turn.text }] };
  });
}

export function converseBody(messages: ConverseMessage[], tools: ToolSpec[] = TOOL_SPECS) {
  return {
    system: [{ text: SYSTEM_PROMPT }],
    messages,
    toolConfig: { tools },
    inferenceConfig: { maxTokens: 2048, temperature: 0.3 },
  };
}

function textOf(message: ConverseMessage | undefined): string {
  return (message?.content ?? [])
    .map((block) => ("text" in block ? block.text : ""))
    .join("")
    .trim();
}

/** Run Converse, executing requested tools, until the model answers or MAX_TOOL_ROUNDS is hit. */
export async function runToolLoop(
  converse: ConverseFn,
  tools: Record<string, ToolFn>,
  initial: ConverseMessage[],
  maxRounds = MAX_TOOL_ROUNDS,
): Promise<AssistantReply> {
  const messages = [...initial];
  const steps: AssistantStep[] = [];
  const sources = new Set<string>();

  for (let round = 0; round <= maxRounds; round++) {
    const response = await converse(messages);
    const message = response.output?.message;
    const toolUses = (message?.content ?? []).flatMap((block) => ("toolUse" in block ? [block.toolUse] : []));

    if (response.stopReason !== "tool_use" || toolUses.length === 0 || round === maxRounds) {
      const reply = textOf(message) ||
        (round === maxRounds && toolUses.length > 0
          ? "I ran out of research steps before finishing. Try a narrower question."
          : "I couldn't come up with an answer. Try rephrasing.");
      return { reply, steps, sources: [...sources] };
    }

    messages.push(message!);
    const results: ContentBlock[] = [];
    for (const use of toolUses) {
      const tool = tools[use.name];
      if (!tool) {
        steps.push({ tool: use.name, summary: "Unknown tool" });
        results.push({
          toolResult: { toolUseId: use.toolUseId, content: [{ text: `Unknown tool ${use.name}` }], status: "error" },
        });
        continue;
      }
      try {
        const result = await tool(use.input ?? {});
        steps.push({ tool: use.name, summary: result.summary });
        result.sources?.forEach((url) => sources.add(url));
        results.push({ toolResult: { toolUseId: use.toolUseId, content: [{ json: { result: result.data } }] } });
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        steps.push({ tool: use.name, summary: `Failed: ${reason}` });
        results.push({ toolResult: { toolUseId: use.toolUseId, content: [{ text: reason }], status: "error" } });
      }
    }
    messages.push({ role: "user", content: results });
  }
  // Unreachable: the loop returns on its last round.
  return { reply: "", steps, sources: [...sources] };
}

export type SearchHit = { title: string; url: string; snippet: string };

/** Top results from a Brave Search web response. */
export function braveResults(body: unknown, limit = 5): SearchHit[] {
  const results = (body as { web?: { results?: unknown[] } })?.web?.results;
  if (!Array.isArray(results)) return [];
  return results
    .map((r) => r as { title?: string; url?: string; description?: string })
    .filter((r) => typeof r.url === "string" && r.url.startsWith("http"))
    .slice(0, limit)
    .map((r) => ({
      title: stripTags(r.title ?? ""),
      url: r.url!,
      snippet: stripTags(r.description ?? ""),
    }));
}

function stripTags(value: string): string {
  return value.replace(/<[^>]*>/g, "").trim();
}

/** PostgREST ilike pattern for free text; strips characters that break or-filters. */
export function ilikePattern(query: string): string | null {
  const cleaned = query.replace(/[%*,()"\\]/g, " ").replace(/\s+/g, " ").trim();
  return cleaned ? `*${cleaned}*` : null;
}

export const MAX_PAGE_CHARS = 8000;

/** Title and clipped markdown from a Firecrawl /v1/scrape response. */
export function firecrawlPage(body: unknown, url: string): { title: string; url: string; markdown: string } {
  const data = (body as { success?: boolean; data?: { markdown?: string; metadata?: { title?: string; sourceURL?: string } } })
    ?.data;
  const markdown = (data?.markdown ?? "").trim();
  if (!markdown) throw new Error("Page had no readable text");
  return {
    title: data?.metadata?.title ?? "",
    url: data?.metadata?.sourceURL ?? url,
    markdown: markdown.length > MAX_PAGE_CHARS ? `${markdown.slice(0, MAX_PAGE_CHARS)}\n…[truncated]` : markdown,
  };
}

/** Only public http(s) URLs; blocks localhost and private-network hosts so the scraper can't be pointed inward. */
export function isPublicHttpUrl(raw: string): boolean {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".internal") || host.endsWith(".local")) {
    return false;
  }
  if (/^(127\.|10\.|0\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host)) return false;
  if (host.includes(":") && (host === "::1" || /^(fc|fd|fe80)/.test(host) || host.startsWith("::ffff:"))) return false;
  return true;
}
