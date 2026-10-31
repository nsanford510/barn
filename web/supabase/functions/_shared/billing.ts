// Runtime helpers for the subscription edge functions.
import Stripe from "https://esm.sh/stripe@18.5.0";
import { createClient, type SupabaseClient, type User } from "https://esm.sh/@supabase/supabase-js@2.49.1";
import { PRICE_CENTS, PRICE_LOOKUP_KEY } from "./subscriptions.ts";

export const corsHeaders = {
  // Auth is a bearer token, not a cookie, so any origin (including the Android app) may call.
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type, x-supabase-client-platform, x-supabase-client-platform-version, x-supabase-client-runtime, x-supabase-client-runtime-version",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

export function requireEnv(name: string): string {
  const value = Deno.env.get(name);
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}

export function stripeClient(): Stripe {
  return new Stripe(requireEnv("STRIPE_SECRET_KEY"), { apiVersion: "2025-08-27.basil" });
}

export function adminClient(): SupabaseClient {
  return createClient(requireEnv("SUPABASE_URL"), requireEnv("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false },
  });
}

/** The Supabase user behind the request's bearer token, or null. */
export async function requestUser(req: Request): Promise<User | null> {
  const authorization = req.headers.get("Authorization");
  if (!authorization) return null;
  const client = createClient(requireEnv("SUPABASE_URL"), requireEnv("SUPABASE_ANON_KEY"), {
    global: { headers: { Authorization: authorization } },
    auth: { persistSession: false },
  });
  const { data, error } = await client.auth.getUser();
  return error ? null : data.user;
}

/** STRIPE_PRICE_ID if set; otherwise the $100/month price, created on first use. */
export async function subscriptionPriceId(stripe: Stripe): Promise<string> {
  const configured = Deno.env.get("STRIPE_PRICE_ID");
  if (configured) return configured;
  const find = async () =>
    (await stripe.prices.list({ lookup_keys: [PRICE_LOOKUP_KEY], active: true, limit: 1 })).data[0]?.id;
  const existing = await find();
  if (existing) return existing;
  try {
    const price = await stripe.prices.create({
      lookup_key: PRICE_LOOKUP_KEY,
      currency: "usd",
      unit_amount: PRICE_CENTS,
      recurring: { interval: "month" },
      product_data: { name: "BARN Scout" },
    });
    return price.id;
  } catch (error) {
    // Another request created it first (lookup keys are unique).
    const raced = await find();
    if (raced) return raced;
    throw error;
  }
}
