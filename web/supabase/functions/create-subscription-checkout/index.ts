// Start a Stripe Checkout session for the BARN Scout subscription ($100/month, 30-day trial).
// Body: { return_path?: string }  ->  { url } or { already_subscribed: true }
import { serve } from "https://deno.land/std@0.190.0/http/server.ts";
import { adminClient, corsHeaders, json, requestUser, requireEnv, stripeClient, subscriptionPriceId } from "../_shared/billing.ts";
import { ACCESS_STATUSES, returnUrl, trialDaysFor } from "../_shared/subscriptions.ts";

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    const user = await requestUser(req);
    if (!user) return json({ error: "Sign in first." }, 401);
    const body = await req.json().catch(() => ({}));

    const stripe = stripeClient();
    const db = adminClient();
    const { data: existing, error: readError } = await db
      .from("subscriptions")
      .select("stripe_customer_id, status")
      .eq("user_id", user.id)
      .maybeSingle();
    if (readError) throw readError;
    if (existing && ACCESS_STATUSES.has(existing.status ?? "")) {
      return json({ already_subscribed: true });
    }

    let customerId = existing?.stripe_customer_id as string | undefined;
    if (!customerId) {
      const customer = await stripe.customers.create(
        { email: user.email, metadata: { supabase_user_id: user.id } },
        { idempotencyKey: `barn-customer-${user.id}` },
      );
      customerId = customer.id;
      const { error } = await db
        .from("subscriptions")
        .upsert({ user_id: user.id, stripe_customer_id: customerId }, { onConflict: "user_id" });
      if (error) throw error;
    }

    const prior = await stripe.subscriptions.list({ customer: customerId, status: "all", limit: 1 });
    const trialDays = trialDaysFor(prior.data.length > 0);
    const siteUrl = requireEnv("SITE_URL");

    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      customer: customerId,
      client_reference_id: user.id,
      line_items: [{ price: await subscriptionPriceId(stripe), quantity: 1 }],
      payment_method_collection: "always",
      subscription_data: {
        metadata: { supabase_user_id: user.id },
        ...(trialDays
          ? { trial_period_days: trialDays, trial_settings: { end_behavior: { missing_payment_method: "cancel" } } }
          : {}),
      },
      success_url: returnUrl(siteUrl, body.return_path, "/billing/done"),
      cancel_url: returnUrl(siteUrl, body.cancel_path ?? body.return_path, "/login"),
    });

    return json({ url: session.url, trial_days: trialDays ?? 0 });
  } catch (error) {
    console.error("create-subscription-checkout failed:", error);
    return json({ error: "Could not start checkout. Try again." }, 500);
  }
});
