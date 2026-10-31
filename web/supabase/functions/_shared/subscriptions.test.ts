// Run with: deno test web/supabase/functions/_shared/subscriptions.test.ts
import { assertEquals } from "https://deno.land/std@0.190.0/testing/asserts.ts";
import { returnUrl, shouldApply, subscriptionRow, trialDaysFor } from "./subscriptions.ts";

Deno.test("subscriptionRow maps Stripe fields and item-level period end", () => {
  const row = subscriptionRow(
    {
      id: "sub_1",
      status: "trialing",
      customer: "cus_1",
      trial_end: 1_800_000_000,
      cancel_at_period_end: false,
      items: { data: [{ price: { id: "price_1" }, current_period_end: 1_800_000_000 }] },
    },
    "user-1",
    new Date("2026-10-31T00:00:00Z"),
  );
  assertEquals(row, {
    user_id: "user-1",
    stripe_customer_id: "cus_1",
    stripe_subscription_id: "sub_1",
    status: "trialing",
    price_id: "price_1",
    trial_end: "2027-01-15T08:00:00.000Z",
    current_period_end: "2027-01-15T08:00:00.000Z",
    cancel_at_period_end: false,
    updated_at: "2026-10-31T00:00:00.000Z",
  });
});

Deno.test("shouldApply keeps a live subscription over a stale dead one", () => {
  const live = { stripe_subscription_id: "sub_new", status: "active" };
  assertEquals(shouldApply(null, { stripe_subscription_id: "sub_1", status: "trialing" }), true);
  assertEquals(shouldApply(live, { stripe_subscription_id: "sub_new", status: "canceled" }), true);
  assertEquals(shouldApply(live, { stripe_subscription_id: "sub_old", status: "canceled" }), false);
  assertEquals(shouldApply({ stripe_subscription_id: "sub_old", status: "canceled" }, live), true);
});

Deno.test("trial only for first-time subscribers", () => {
  assertEquals(trialDaysFor(false), 30);
  assertEquals(trialDaysFor(true), undefined);
});

Deno.test("returnUrl only accepts same-site paths", () => {
  const site = "https://barn-scan.vercel.app/";
  assertEquals(returnUrl(site, "/billing/done"), "https://barn-scan.vercel.app/billing/done");
  assertEquals(returnUrl(site, "/login?subscribed=1"), "https://barn-scan.vercel.app/login?subscribed=1");
  for (const evil of ["//evil.example", "https://evil.example", "/\\evil", "evil", 42, undefined]) {
    assertEquals(returnUrl(site, evil, "/x"), "https://barn-scan.vercel.app/x");
  }
});
