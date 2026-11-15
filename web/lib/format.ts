import type { AuditEvent, RfqState } from "@/lib/types";

export const stateLabel: Record<RfqState, string> = {
  intake: "Intake",
  normalized: "Normalized",
  enriched_with_memory: "Supplier memory",
  analyzed: "Analyzed",
  negotiation_or_ready: "Ready for review",
  awaiting_approval: "Awaiting buyer",
  approved_draft: "Draft approved",
  rejected: "Rejected",
};

export function formatMoney(value: string | number, currency = "USD") {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";

  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

export function formatDateTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

export function auditEventLabel(event: AuditEvent) {
  return event.event_type.replaceAll("_", " ");
}

export function sumCosts(...values: Array<string | number>) {
  return values.reduce<number>((total, value) => total + Number(value), 0);
}
