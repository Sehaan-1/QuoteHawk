import type { RfqRunDetail } from "./types";

const configuredBase = process.env.EXPO_PUBLIC_QUOTEHAWK_API_BASE_URL;
const apiBase = `${configuredBase?.replace(/\/$/, "") ?? "http://10.0.2.2:8000"}/api/rfq`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const responseBody = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(responseBody.detail ?? `QuoteHawk API returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export const getRun = (runId: string) => request<RfqRunDetail>(`/runs/${encodeURIComponent(runId)}`);

export const openApproval = (runId: string) =>
  request(`/runs/${encodeURIComponent(runId)}/approval-requests`, { method: "POST" });

export const submitDecision = (
  runId: string,
  buyerId: string,
  decision: "approve" | "reject",
  selectedQuoteId?: string,
) =>
  request(`/runs/${encodeURIComponent(runId)}/approvals`, {
    method: "POST",
    body: JSON.stringify({
      buyer_id: buyerId,
      decision,
      selected_quote_id: decision === "approve" ? selectedQuoteId : undefined,
    }),
  });
