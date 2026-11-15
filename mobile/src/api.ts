import type { BuyerDecision, RfqRunDetail } from "./types";

const configuredBase = process.env.EXPO_PUBLIC_QUOTEHAWK_API_BASE_URL?.trim();
const fallbackOrigin = "http://10.0.2.2:8000";
const apiBase = `${(configuredBase || fallbackOrigin).replace(/\/$/, "")}/api/rfq`;

export class QuoteHawkApiError extends Error {
  readonly status: number;
  readonly retryable: boolean;

  constructor(message: string, status: number) {
    super(message);
    this.name = "QuoteHawkApiError";
    this.status = status;
    this.retryable = status === 0 || status === 408 || status === 429 || status >= 500;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new QuoteHawkApiError(
      controller.signal.aborted
        ? "QuoteHawk did not respond within 15 seconds. Try again."
        : "Could not reach QuoteHawk. Check the device-reachable API origin and try again.",
      0,
    );
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const responseBody = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new QuoteHawkApiError(
      responseBody.detail ?? `QuoteHawk API returned ${response.status}`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export const getRun = (runId: string) =>
  request<RfqRunDetail>(`/runs/${encodeURIComponent(runId)}`);

export const openApproval = (runId: string) =>
  request(`/runs/${encodeURIComponent(runId)}/approval-requests`, { method: "POST" });

export const submitDecision = (
  runId: string,
  prototypeBuyerId: string | undefined,
  decision: BuyerDecision,
  selectedQuoteId?: string,
  note?: string,
) =>
  request(`/runs/${encodeURIComponent(runId)}/approvals`, {
    method: "POST",
    body: JSON.stringify({
      buyer_id: prototypeBuyerId,
      decision,
      selected_quote_id: decision === "reject" ? undefined : selectedQuoteId,
      note: note || undefined,
    }),
  });
