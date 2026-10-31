// Keep public.subscriptions in sync with Stripe. Configure in Stripe with these events:
// checkout.session.completed, customer.subscription.created, customer.subscription.updated,
// customer.subscription.deleted, customer.subscription.paused, customer.subscription.resumed.
import { serve } from "https://deno.land/std@0.190.0/http/server.ts";
import Stripe from "https://esm.sh/stripe@18.5.0";
import { adminClient, requireEnv, stripeClient } from "../_shared/billing.ts";
import { customerId, shouldApply, subscriptionRow } from "../_shared/subscriptions.ts";

const cryptoProvider = Stripe.createSubtleCryptoProvider();

serve(async (req) => {
  if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });

  const stripe = stripeClient();
  const signature = req.headers.get("Stripe-Signature");
  const payload = await req.text();
  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      payload,
      signature ?? "",
      requireEnv("STRIPE_WEBHOOK_SECRET"),
      undefined,
      cryptoProvider,
    );
  } catch (error) {
    console.error("Rejected Stripe webhook:", (error as Error).message);
    return new Response("Invalid signature", { status: 400 });
  }

  let subscriptionId: string | null = null;
  if (event.type === "checkout.session.completed") {
    const session = event.data.object as Stripe.Checkout.Session;
    if (session.mode === "subscription" && session.subscription) {
      subscriptionId = typeof session.subscription === "string" ? session.subscription : session.subscription.id;
    }
  } else if (event.type.startsWith("customer.subscription.")) {
    subscriptionId = (event.data.object as Stripe.Subscription).id;
  }
  if (!subscriptionId) return new Response("ignored", { status: 200 });

  try {
    await syncSubscription(stripe, subscriptionId);
    return new Response("ok", { status: 200 });
  } catch (error) {
    console.error(`Failed to sync subscription ${subscriptionId} (${event.type}):`, error);
    return new Response("sync failed", { status: 500 }); // Stripe retries
  }
});

async function syncSubscription(stripe: Stripe, subscriptionId: string) {
  // Events can arrive out of order, so read the current state instead of trusting the payload.
  const sub = await stripe.subscriptions.retrieve(subscriptionId);
  const db = adminClient();

  let userId = sub.metadata?.supabase_user_id;
  if (!userId) {
    const { data } = await db
      .from("subscriptions")
      .select("user_id")
      .eq("stripe_customer_id", customerId(sub))
      .maybeSingle();
    userId = data?.user_id;
  }
  if (!userId) {
    console.warn(`Subscription ${sub.id} has no BARN user; ignoring.`);
    return;
  }

  const { data: existing, error: readError } = await db
    .from("subscriptions")
    .select("stripe_subscription_id, status")
    .eq("user_id", userId)
    .maybeSingle();
  if (readError) throw readError;

  const row = subscriptionRow(sub, userId);
  if (!shouldApply(existing, row)) {
    console.info(`Kept newer subscription for ${userId}; skipped stale ${sub.id} (${sub.status}).`);
    return;
  }
  const { error } = await db.from("subscriptions").upsert(row, { onConflict: "user_id" });
  if (error) throw error;
}
