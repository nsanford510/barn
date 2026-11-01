// Scout research assistant: Kimi K3 on Amazon Bedrock with BARN data and web tools.
// Body: { messages: [{ role: "user" | "assistant", text }], apn?: string }
//   ->  { reply, steps: [{ tool, summary }], sources: [url] }
// Secrets: BEDROCK_API_KEY, BRAVE_API_KEY; optional AWS_REGION (us-west-2), ASSISTANT_MODEL_ID,
// FIRECRAWL_URL + FIRECRAWL_KEY (enables read_page).
import { serve } from "https://deno.land/std@0.190.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import { adminClient, corsHeaders, json, requestUser, requireEnv } from "../_shared/billing.ts";
import {
  braveResults,
  buildMessages,
  type ConverseMessage,
  type ConverseResponse,
  converseBody,
  DEFAULT_MODEL_ID,
  firecrawlPage,
  ilikePattern,
  isPublicHttpUrl,
  runToolLoop,
  type ToolFn,
  toolSpecsFor,
  trimHistory,
} from "../_shared/assistant.ts";

const PROPERTY_COLUMNS =
  "apn,location,city,situs_zip,zip_code,lat,lng,has_vpt,delinquent,power_status,condition_score," +
  "research_status,owner_name,deceased_count,last_sale_date,tax_year,last_payment,mailing_address," +
  "is_out_of_state,is_scouted,ai_vacancy_verdict,ai_vacancy_confidence,site_vacant";
const SEARCH_COLUMNS = "apn,location,city,owner_name,mailing_address,is_out_of_state,deceased_count";
const CONTACT_COLUMNS =
  "owner_mobile_phone,owner_details_url,property_search_url,prop_ownership_type,prop_occupancy_type";

async function hasAppAccess(authorization: string): Promise<boolean> {
  const client = createClient(requireEnv("SUPABASE_URL"), requireEnv("SUPABASE_ANON_KEY"), {
    global: { headers: { Authorization: authorization } },
    auth: { persistSession: false },
  });
  const { data, error } = await client.rpc("has_app_access");
  if (error) throw error;
  return data === true;
}

async function fetchJson(url: string, init: RequestInit, label: string, timeoutMs = 60_000): Promise<unknown> {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
  const text = await response.text();
  if (!response.ok) throw new Error(`${label} ${response.status}: ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : {};
}

function bedrockConverse(): (messages: ConverseMessage[]) => Promise<ConverseResponse> {
  const region = Deno.env.get("AWS_REGION") || "us-west-2";
  const model = Deno.env.get("ASSISTANT_MODEL_ID") || DEFAULT_MODEL_ID;
  const apiKey = requireEnv("BEDROCK_API_KEY");
  const url = `https://bedrock-runtime.${region}.amazonaws.com/model/${encodeURIComponent(model)}/converse`;
  return async (messages) =>
    await fetchJson(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify(converseBody(messages, toolSpecsFor(Object.keys(tools())))),
    }, "Bedrock", 120_000) as ConverseResponse;
}

function str(input: Record<string, unknown>, key: string): string {
  const value = input[key];
  return typeof value === "string" ? value.trim() : "";
}

// BARN reads use the service role: the caller already passed has_app_access(), which is the same gate the
// app's own data access uses, and some research tables (vacancy_findings) are not exposed to `authenticated`.
const barnTools: Record<string, ToolFn> = {
  async get_property(input) {
    const apn = str(input, "apn");
    if (!apn) throw new Error("apn is required");
    const db = adminClient();
    const { data, error } = await db.from("map_markers").select(PROPERTY_COLUMNS).eq("apn", apn).maybeSingle();
    if (error) throw error;
    if (!data) return { data: null, summary: `No BARN property ${apn}` };
    const contact = await db.from("bills").select(CONTACT_COLUMNS).eq("apn", apn).maybeSingle();
    // Contact columns may be missing on some databases; the property alone is still useful.
    const contactFields = contact.error ? {} : (contact.data ?? {}) as unknown as Record<string, unknown>;
    const merged = { ...(data as unknown as Record<string, unknown>), ...contactFields };
    return { data: merged, summary: `Property ${apn}` };
  },

  async search_properties(input) {
    const pattern = ilikePattern(str(input, "query"));
    if (!pattern) throw new Error("query is required");
    let query = adminClient()
      .from("map_markers")
      .select(SEARCH_COLUMNS)
      .or(`location.ilike.${pattern},owner_name.ilike.${pattern},apn.ilike.${pattern}`)
      .limit(10);
    const city = str(input, "city");
    if (city) query = query.ilike("city", city);
    const { data, error } = await query;
    if (error) throw error;
    return { data: data ?? [], summary: `Searched BARN for "${str(input, "query")}" (${data?.length ?? 0})` };
  },

  async get_vacancy_findings(input) {
    const apn = str(input, "apn");
    if (!apn) throw new Error("apn is required");
    const { data, error } = await adminClient()
      .from("vacancy_findings")
      .select("agent,verdict,confidence,rationale,evidence,created_at")
      .eq("apn", apn)
      .order("created_at", { ascending: false })
      .limit(5);
    if (error) throw error;
    return { data: data ?? [], summary: `Vacancy findings for ${apn} (${data?.length ?? 0})` };
  },
};

async function webSearch(input: Record<string, unknown>) {
  const query = str(input, "query");
  if (!query) throw new Error("query is required");
  const url = new URL("https://api.search.brave.com/res/v1/web/search");
  url.searchParams.set("q", query.slice(0, 400));
  url.searchParams.set("count", "8");
  const body = await fetchJson(url.toString(), {
    headers: { Accept: "application/json", "X-Subscription-Token": requireEnv("BRAVE_API_KEY") },
  }, "Brave", 20_000);
  const hits = braveResults(body);
  return { data: hits, summary: `Web search "${query}" (${hits.length})`, sources: hits.map((h) => h.url) };
}

async function readPage(input: Record<string, unknown>) {
  const target = str(input, "url");
  if (!isPublicHttpUrl(target)) throw new Error("Only public http(s) URLs can be read");
  const base = requireEnv("FIRECRAWL_URL").replace(/\/+$/, "");
  const key = Deno.env.get("FIRECRAWL_KEY");
  const body = await fetchJson(`${base}/v1/scrape`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(key ? { Authorization: `Bearer ${key}` } : {}) },
    body: JSON.stringify({ url: target, formats: ["markdown"], onlyMainContent: true, timeout: 45_000 }),
  }, "Firecrawl", 60_000);
  const page = firecrawlPage(body, target);
  return { data: page, summary: `Read ${new URL(page.url).hostname}`, sources: [page.url] };
}

function tools(): Record<string, ToolFn> {
  return {
    ...barnTools,
    web_search: webSearch,
    ...(Deno.env.get("FIRECRAWL_URL") ? { read_page: readPage } : {}),
  };
}

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    const user = await requestUser(req);
    if (!user) return json({ error: "Sign in first." }, 401);
    if (!(await hasAppAccess(req.headers.get("Authorization")!))) {
      return json({ error: "An active BARN Scout subscription is required." }, 403);
    }

    const body = await req.json().catch(() => ({}));
    const history = trimHistory(body.messages);
    if (history.length === 0 || history[history.length - 1].role !== "user") {
      return json({ error: "Send a question." }, 400);
    }
    const apn = typeof body.apn === "string" && body.apn.trim() ? body.apn.trim().slice(0, 64) : null;

    const result = await runToolLoop(bedrockConverse(), tools(), buildMessages(history, apn));
    return json(result);
  } catch (error) {
    console.error("property-assistant failed:", error);
    return json({ error: "The assistant couldn't answer right now. Try again." }, 500);
  }
});
