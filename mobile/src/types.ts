export type RfqState =
  | "negotiation_or_ready"
  | "awaiting_approval"
  | "approved_draft"
  | "rejected"
  | string;

export type QuoteScore = {
  quote_id: string;
  vendor_id: string;
  landed_cost: { total_landed_cost: string };
  weighted_score: string;
  approval_ready: boolean;
  guardrail_messages: string[];
};

export type RfqRunDetail = {
  run: {
    run_id: string;
    requisition_id: string;
    state: RfqState;
  };
  payload: {
    requisition: { currency: string; max_landed_cost: string };
    quotes: Array<{ quote_id: string; vendor_name: string; lead_time_days: number }>;
    comparison: { ranked_quotes: QuoteScore[] };
  };
  purchase_order_draft: {
    vendor_name: string;
    total_landed_cost: string;
    disclaimer: string;
  } | null;
};
