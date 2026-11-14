import type { CounterOfferDraft, RfqRunDetail } from "@/lib/types";

const apiBase = "/api/rfq";

const demoRequisition = {
  requisition_id: "PR-1001",
  currency: "USD",
  items: [
    {
      sku: "FILTER-10",
      description: "Industrial filter cartridge",
      quantity: "100",
      unit_of_measure: "each",
    },
  ],
  max_landed_cost: "1400.00",
  target_lead_time_days: 7,
};

const demoQuotes = [
  {
    quote_id: "DEMO-ALPHA",
    vendor_id: "vendor-alpha",
    vendor_name: "Alpha Industrial",
    source: "manual",
    currency: "USD",
    lines: [{ sku: "FILTER-10", quoted_quantity: "100", unit_price: "10.00" }],
    freight_cost: "100.00",
    duties_cost: "50.00",
    lead_time_days: 7,
    payment_terms: "net_30",
  },
  {
    quote_id: "DEMO-BRAVO",
    vendor_id: "vendor-bravo",
    vendor_name: "Bravo Components",
    source: "manual",
    currency: "USD",
    lines: [{ sku: "FILTER-10", quoted_quantity: "100", unit_price: "9.50" }],
    freight_cost: "130.00",
    duties_cost: "40.00",
    lead_time_days: 9,
    payment_terms: "net_30",
  },
  {
    quote_id: "DEMO-COPPER",
    vendor_id: "vendor-copper",
    vendor_name: "Copperline Supply",
    source: "manual",
    currency: "USD",
    lines: [{ sku: "FILTER-10", quoted_quantity: "100", unit_price: "10.35" }],
    freight_cost: "90.00",
    duties_cost: "40.00",
    lead_time_days: 6,
    payment_terms: "net_45",
  },
  {
    quote_id: "DEMO-DELTA",
    vendor_id: "vendor-delta",
    vendor_name: "Delta Process Goods",
    source: "manual",
    currency: "USD",
    lines: [{ sku: "FILTER-10", quoted_quantity: "100", unit_price: "11.00" }],
    freight_cost: "100.00",
    duties_cost: "60.00",
    lead_time_days: 5,
    payment_terms: "net_60",
  },
  {
    quote_id: "DEMO-EMBER",
    vendor_id: "vendor-ember",
    vendor_name: "Ember Filtration",
    source: "manual",
    currency: "USD",
    lines: [{ sku: "FILTER-10", quoted_quantity: "100", unit_price: "13.00" }],
    freight_cost: "110.00",
    duties_cost: "50.00",
    lead_time_days: 7,
    payment_terms: "net_30",
  },
];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiBase}${path}`, { ...init, headers });
  if (!response.ok) {
    const responseBody = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(responseBody.detail ?? `QuoteHawk API returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export const getRun = (runId: string) =>
  request<RfqRunDetail>(`/runs/${encodeURIComponent(runId)}`);

export const createDemoRun = (runId: string) =>
  request<{ run: { run_id: string } }>("/runs", {
    method: "POST",
    body: JSON.stringify({
      run_id: runId,
      requisition: demoRequisition,
      quotes: demoQuotes,
      observed_on: new Date().toISOString().slice(0, 10),
    }),
  });

export const openApproval = (runId: string) =>
  request(`/runs/${encodeURIComponent(runId)}/approval-requests`, { method: "POST" });

export const decideApproval = (
  runId: string,
  decision: "approve" | "reject",
  buyerId: string,
  selectedQuoteId: string,
  note: string,
) =>
  request(`/runs/${encodeURIComponent(runId)}/approvals`, {
    method: "POST",
    body: JSON.stringify({
      buyer_id: buyerId,
      decision,
      selected_quote_id: decision === "approve" ? selectedQuoteId : undefined,
      note: note || undefined,
    }),
  });

export const draftCounterOffer = (runId: string, vendorId: string, targetLandedCost: string) =>
  request<CounterOfferDraft>(`/runs/${encodeURIComponent(runId)}/counter-offers`, {
    method: "POST",
    body: JSON.stringify({
      vendor_id: vendorId,
      target_landed_cost: targetLandedCost,
      provider: "fixture",
    }),
  });
