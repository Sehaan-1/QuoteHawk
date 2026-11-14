export type RfqState =
  | "intake"
  | "normalized"
  | "enriched_with_memory"
  | "analyzed"
  | "negotiation_or_ready"
  | "awaiting_approval"
  | "approved_draft"
  | "rejected";

export type LandedCost = {
  currency: string;
  line_total: string;
  freight_cost: string;
  duties_cost: string;
  lead_time_penalty: string;
  minimum_order_penalty: string;
  payment_term_penalty: string;
  quality_penalty: string;
  early_payment_discount: string;
  total_landed_cost: string;
};

export type QuoteScore = {
  quote_id: string;
  vendor_id: string;
  landed_cost: LandedCost;
  weighted_score: string;
  price_score: string;
  lead_time_score: string;
  payment_term_score: string;
  supplier_performance_score: string;
  approval_ready: boolean;
  guardrail_messages: string[];
};

export type Quote = {
  quote_id: string;
  vendor_id: string;
  vendor_name: string;
  lead_time_days: number;
};

export type AuditEvent = {
  sequence: number;
  occurred_at: string;
  event_type: string;
  reason: string;
};

export type QuoteAnomaly = {
  quote_id: string;
  metric: string;
  severity: string;
  explanation: string;
};

export type CounterOfferDraft = {
  run_id: string;
  vendor_id: string;
  subject: string;
  body: string;
  target_landed_cost: string;
  currency: string;
  status: "draft_only";
  disclosure_check: string;
};

export type RfqRunDetail = {
  run: {
    run_id: string;
    requisition_id: string;
    state: RfqState;
  };
  audit_events: AuditEvent[];
  payload: {
    requisition: { currency: string; max_landed_cost: string };
    quotes: Quote[];
    comparison: { currency: string; ranked_quotes: QuoteScore[] };
    anomalies: QuoteAnomaly[];
  };
  purchase_order_draft: {
    vendor_name: string;
    total_landed_cost: string;
    disclaimer: string;
  } | null;
};
