// Pure helpers shared by the subscription edge functions (no Deno/npm imports, so they test anywhere).

export const PRICE_LOOKUP_KEY = "barn_scout_monthly";
export const PRICE_CENTS = 100_00; // $100.00
export const TRIAL_DAYS = 30;

/** Stripe statuses that grant access. Keep in sync with public.has_active_subscription(). */
export const ACCESS_STATUSES = new Set(["trialing", "active"]);

export interface SubscriptionRow {
  user_id: string;
  stripe_customer_id: string;
  stripe_subscription_id: string;
  status: string;
  price_id: string | null;
  trial_end: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  updated_at: string;
}

/** The parts of a Stripe.Subscription we read. */
export interface StripeSubscriptionLike {
  id: string;
  status: string;
  customer: string | { id: string };
  trial_end: number | null;
  cancel_at_period_end: boolean;
  metadata?: Record<string, string> | null;
  current_period_end?: number | null;
  items?: { data: Array<{ price?: { id: string } | null; current_period_end?: number | null }> };
}

const isoOrNull = (seconds: number | null | undefined) =>
  typeof seconds === "number" ? new Date(seconds * 1000).toISOString() : null;

export function customerId(sub: StripeSubscriptionLike): string {
  return typeof sub.customer === "string" ? sub.customer : sub.customer.id;
}

export function subscriptionRow(sub: StripeSubscriptionLike, userId: string, now = new Date()): SubscriptionRow {
  const item = sub.items?.data?.[0];
  return {
    user_id: userId,
    stripe_customer_id: customerId(sub),
    stripe_subscription_id: sub.id,
    status: sub.status,
    price_id: item?.price?.id ?? null,
    trial_end: isoOrNull(sub.trial_end),
    // Newer Stripe API versions moved current_period_end onto the subscription item.
    current_period_end: isoOrNull(item?.current_period_end ?? sub.current_period_end),
    cancel_at_period_end: sub.cancel_at_period_end,
    updated_at: now.toISOString(),
  };
}

/**
 * Whether an update for `incoming` should overwrite the stored row. A late event about an
 * old, dead subscription must not wipe out a newer subscription that still grants access.
 */
export function shouldApply(
  existing: Pick<SubscriptionRow, "stripe_subscription_id" | "status"> | null,
  incoming: Pick<SubscriptionRow, "stripe_subscription_id" | "status">,
): boolean {
  if (!existing?.stripe_subscription_id) return true;
  if (existing.stripe_subscription_id === incoming.stripe_subscription_id) return true;
  return !(ACCESS_STATUSES.has(existing.status) && !ACCESS_STATUSES.has(incoming.status));
}

/** Only first-time subscribers get the free trial. */
export function trialDaysFor(hasPriorSubscription: boolean): number | undefined {
  return hasPriorSubscription ? undefined : TRIAL_DAYS;
}

/**
 * Build a redirect back to our own site. Callers pass only a path, so Checkout and the
 * billing portal can never be turned into an open redirect.
 */
export function returnUrl(siteUrl: string, path: unknown, fallbackPath = "/"): string {
  const base = siteUrl.replace(/\/+$/, "");
  const p = typeof path === "string" && /^\/(?![\/\\])[^\s\\]*$/.test(path) ? path : fallbackPath;
  return base + p;
}
