// Open the Stripe customer portal so a subscriber can update payment details or cancel.
// Body: { return_path?: string }  ->  { url }
import { serve } from "https://deno.land/std@0.190.0/http/server.ts";
import { adminClient, corsHeaders, json, requestUser, requireEnv, stripeClient } from "../_shared/billing.ts";
import { returnUrl } from "../_shared/subscriptions.ts";

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    const user = await requestUser(req);
    if (!user) return json({ error: "Sign in first." }, 401);
    const body = await req.json().catch(() => ({}));

    const { data, error } = await adminClient()
      .from("subscriptions")
      .select("stripe_customer_id")
      .eq("user_id", user.id)
      .maybeSingle();
    if (error) throw error;
    if (!data?.stripe_customer_id) return json({ error: "No subscription found for this account." }, 404);

    const session = await stripeClient().billingPortal.sessions.create({
      customer: data.stripe_customer_id,
      return_url: returnUrl(requireEnv("SITE_URL"), body.return_path, "/billing/done"),
    });
    return json({ url: session.url });
  } catch (error) {
    console.error("create-billing-portal failed:", error);
    return json({ error: "Could not open billing. Try again." }, 500);
  }
});
