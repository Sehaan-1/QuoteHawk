"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  createDemoRun,
  decideApproval,
  draftCounterOffer,
  getRun,
  openApproval,
} from "@/lib/api";
import type { AuditEvent, CounterOfferDraft, QuoteScore, RfqRunDetail, RfqState } from "@/lib/types";

const stateLabel: Record<RfqState, string> = {
  intake: "Intake",
  normalized: "Normalized",
  enriched_with_memory: "Supplier memory",
  analyzed: "Analyzed",
  negotiation_or_ready: "Ready for review",
  awaiting_approval: "Awaiting buyer",
  approved_draft: "Draft approved",
  rejected: "Rejected",
};

const money = (value: string | number, currency = "USD") =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));

const dateTime = (value: string) =>
  new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

function eventLabel(event: AuditEvent) {
  return event.event_type.replaceAll("_", " ");
}

export function RfqWorkspace() {
  const [runId, setRunId] = useState("");
  const [detail, setDetail] = useState<RfqRunDetail | null>(null);
  const [streamEvents, setStreamEvents] = useState<AuditEvent[]>([]);
  const [buyerId, setBuyerId] = useState("demo-buyer");
  const [selectedQuoteId, setSelectedQuoteId] = useState("");
  const [note, setNote] = useState("");
  const [counterOffer, setCounterOffer] = useState<CounterOfferDraft | null>(null);
  const [message, setMessage] = useState("Load an RFQ run or create a local fixture to begin.");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  const comparison = detail?.payload.comparison;
  const quotes = detail?.payload.quotes ?? [];
  const scores = comparison?.ranked_quotes ?? [];
  const currency = comparison?.currency ?? "USD";
  const totalBudget = detail?.payload.requisition.max_landed_cost ?? "0";
  const eligibleScores = scores.filter((score) => score.approval_ready);
  const selectedScore = scores.find((score) => score.quote_id === selectedQuoteId);
  const selectedVendor = quotes.find((quote) => quote.quote_id === selectedQuoteId);
  const bestScore = scores[0];

  useEffect(() => {
    if (!detail) return;
    const eventSource = new EventSource(`/api/rfq/runs/${encodeURIComponent(detail.run.run_id)}/events`);
    eventSource.addEventListener("rfq.audit", (event) => {
      const auditEvent = JSON.parse(event.data) as AuditEvent;
      setStreamEvents((current) =>
        current.some((candidate) => candidate.sequence === auditEvent.sequence)
          ? current
          : [...current, auditEvent],
      );
    });
    eventSource.addEventListener("rfq.complete", () => eventSource.close());
    eventSource.onerror = () => eventSource.close();
    return () => eventSource.close();
  }, [detail?.run.run_id]);

  useEffect(() => {
    if (!detail) return;
    const firstEligible = detail.payload.comparison.ranked_quotes.find(
      (score) => score.approval_ready,
    );
    setSelectedQuoteId(firstEligible?.quote_id ?? "");
    setStreamEvents(detail.audit_events);
    setCounterOffer(null);
  }, [detail]);

  const surfacedEvents = useMemo(
    () => [...streamEvents].sort((left, right) => left.sequence - right.sequence),
    [streamEvents],
  );

  async function loadRun(nextRunId = runId) {
    const normalized = nextRunId.trim();
    if (!normalized) {
      setError("Enter an RFQ run ID first.");
      return;
    }
    setWorking(true);
    setError("");
    try {
      const result = await getRun(normalized);
      setDetail(result);
      setRunId(result.run.run_id);
      setMessage(`Loaded ${result.run.run_id}. All values below come from the persisted run.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load the RFQ run.");
    } finally {
      setWorking(false);
    }
  }

  async function loadDemo() {
    const demoRunId = `demo-${Date.now()}`;
    setWorking(true);
    setError("");
    try {
      await createDemoRun(demoRunId);
      await loadRun(demoRunId);
      setMessage("Created a local five-bid fixture. Nothing was sent to a supplier or ERP.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create the fixture run.");
      setWorking(false);
    }
  }

  async function requestApproval() {
    if (!detail) return;
    setWorking(true);
    setError("");
    try {
      await openApproval(detail.run.run_id);
      await loadRun(detail.run.run_id);
      setMessage("Buyer review is open. Selecting a quote still creates a non-binding draft only.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not open buyer review.");
    } finally {
      setWorking(false);
    }
  }

  async function submitDecision(decision: "approve" | "reject") {
    if (!detail) return;
    if (decision === "approve" && !selectedQuoteId) {
      setError("Select an eligible quote before approving a draft.");
      return;
    }
    setWorking(true);
    setError("");
    try {
      await decideApproval(
        detail.run.run_id,
        decision,
        buyerId.trim(),
        selectedQuoteId,
        note.trim(),
      );
      await loadRun(detail.run.run_id);
      setMessage(
        decision === "approve"
          ? "The buyer decision created a non-binding PO draft. No order was issued."
          : "The buyer rejected this RFQ outcome. No draft was created.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not record the buyer decision.");
    } finally {
      setWorking(false);
    }
  }

  async function prepareCounterOffer() {
    if (!detail || !selectedVendor || !selectedScore) {
      setError("Choose a quote before preparing a counter-offer.");
      return;
    }
    const target = (Number(selectedScore.landed_cost.total_landed_cost) * 0.95).toFixed(2);
    setWorking(true);
    setError("");
    try {
      const draft = await draftCounterOffer(detail.run.run_id, selectedVendor.vendor_id, target);
      setCounterOffer(draft);
      setMessage("Prepared a fixture counter-offer draft. QuoteHawk did not send it.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not prepare the counter-offer draft.");
    } finally {
      setWorking(false);
    }
  }

  function handleLoad(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void loadRun();
  }

  return (
    <main id="top">
      <header className="site-header">
        <div className="wrap header-inner">
          <a className="brand" href="#top" aria-label="QuoteHawk workspace home">
            <span aria-hidden="true" className="brand-mark">Q</span>
            <span>QuoteHawk</span>
          </a>
          <nav aria-label="Workspace sections">
            <a href="#comparison">Comparison</a>
            <a href="#review">Review</a>
            <a href="#decision">Decision</a>
          </nav>
          <a className="header-action" href="#load-run">Load RFQ run</a>
        </div>
      </header>

      <section className="hero" data-enter aria-labelledby="hero-title">
        <div className="wrap hero-grid">
          <div className="hero-copy">
            <p className="eyebrow">Buyer workspace · draft-only</p>
            <h1 id="hero-title">RFQ approval workspace for tail spend</h1>
            <p className="hero-lede">
              Compare up to five supplier bids, see landed-cost guardrails, and route only an
              eligible choice to a buyer review.
            </p>
            <div className="hero-actions">
              <a className="button button-primary" href="#load-run">Load RFQ run</a>
              <a className="text-link" href="#decision">See the approval boundary</a>
            </div>
            <p className="boundary-note">No supplier email, ERP write-back, or binding purchase order.</p>
          </div>
          <div className="hero-visual" aria-label="A QuoteHawk RFQ comparison preview">
            <div className="app-window hero-window">
              <div className="window-bar">
                <span className="window-dot" /><span className="window-dot" /><span className="window-dot" />
                <span>PR-1001 · bid review</span>
                <span className="window-status">Policy checked</span>
              </div>
              <div className="window-body">
                <aside className="app-sidebar" aria-hidden="true">
                  <span className="side-active">RFQs</span><span>Drafts</span><span>Audit</span>
                </aside>
                <div className="preview-table">
                  <div className="preview-head"><span>Supplier</span><span>Landed cost</span><span>Decision</span></div>
                  <div className="preview-row preview-row--active"><b>Alpha Industrial</b><strong>$1,150.00</strong><span className="tag tag-ready">Eligible</span></div>
                  <div className="preview-row"><b>Bravo Components</b><strong>$1,140.00</strong><span className="tag tag-ready">Eligible</span></div>
                  <div className="preview-row"><b>Ember Filtration</b><strong>$1,470.00</strong><span className="tag tag-blocked">Over cap</span></div>
                </div>
              </div>
            </div>
            <div className="hero-proof" aria-label="Draft-only outcome">
              <span className="proof-dot" aria-hidden="true" />
              <div><strong>Buyer gate</strong><span>Draft only</span></div>
            </div>
          </div>
        </div>
      </section>

      <section className="load-section" id="load-run" aria-labelledby="load-title">
        <div className="wrap load-grid">
          <div>
            <h2 id="load-title">Load a persisted RFQ</h2>
            <p>Enter a run ID from the API, or build a five-bid fixture in this local prototype.</p>
          </div>
          <form className="load-form" onSubmit={handleLoad}>
            <label htmlFor="run-id">RFQ run ID</label>
            <div className="input-row">
              <input
                id="run-id"
                value={runId}
                onChange={(event) => setRunId(event.target.value)}
                placeholder="rfq-…"
                autoComplete="off"
              />
              <button className="button button-primary" type="submit" disabled={working}>Load RFQ run</button>
            </div>
            <button className="button button-secondary" type="button" onClick={() => void loadDemo()} disabled={working}>
              Create local fixture
            </button>
          </form>
        </div>
        <div className="wrap status-row" aria-live="polite">
          <span className="status-signal" aria-hidden="true" />
          <span>{error || message}</span>
        </div>
      </section>

      <section className="workspace-section" id="comparison" aria-labelledby="comparison-title">
        <div className="wrap section-heading">
          <h2 id="comparison-title">Landed cost before preference</h2>
          <p>Each supplier remains visible. Only a bid inside the hard budget can move to buyer review.</p>
        </div>
        <div className="wrap">
          {!detail ? <EmptyComparison /> : (
            <>
              <div className="run-strip">
                <div><span>Run</span><strong>{detail.run.run_id}</strong></div>
                <div><span>Requisition</span><strong>{detail.run.requisition_id}</strong></div>
                <div><span>Budget ceiling</span><strong>{money(totalBudget, currency)}</strong></div>
                <div><span>Workflow state</span><strong className={`state state-${detail.run.state}`}>{stateLabel[detail.run.state]}</strong></div>
              </div>
              <div className="comparison-table" role="region" aria-label="Supplier comparison" tabIndex={0}>
                <div className="table-row table-head"><span>Supplier</span><span>Landed cost</span><span>Score</span><span>Lead time</span><span>Approval</span></div>
                {scores.map((score) => {
                  const quote = quotes.find((candidate) => candidate.quote_id === score.quote_id);
                  return (
                    <button
                      className={`table-row table-data ${selectedQuoteId === score.quote_id ? "is-selected" : ""}`}
                      key={score.quote_id}
                      onClick={() => setSelectedQuoteId(score.quote_id)}
                      type="button"
                    >
                      <span><b>{quote?.vendor_name ?? score.vendor_id}</b><small>{score.quote_id}</small></span>
                      <span>{money(score.landed_cost.total_landed_cost, currency)}</span>
                      <span>{Number(score.weighted_score).toFixed(1)}</span>
                      <span>{quote?.lead_time_days ?? "—"} days</span>
                      <span><i className={`tag ${score.approval_ready ? "tag-ready" : "tag-blocked"}`}>{score.approval_ready ? "Eligible" : "Blocked"}</i></span>
                    </button>
                  );
                })}
              </div>
              {bestScore && <CostBreakdown score={bestScore} currency={currency} />}
            </>
          )}
        </div>
      </section>

      <section className="review-section" id="review" data-moment="iris-opens" data-moment-opts='{"from":"base"}' aria-labelledby="review-title">
        <div className="wrap section-heading section-heading-light">
          <p className="eyebrow">Review record</p>
          <h2 id="review-title">Exceptions and audit events, without hidden reasoning</h2>
          <p>The workspace shows persisted calculations and policy facts. Model traces are never displayed.</p>
        </div>
        <div className="wrap review-grid">
          <div className="anomaly-panel">
            <div className="panel-title"><span>Supplier anomalies</span><span>{detail?.payload.anomalies?.length ?? 0} flagged</span></div>
            {!detail ? <p className="muted-copy">Load a run to inspect explainable deviations from supplier history.</p> : (
              detail.payload.anomalies?.length ? detail.payload.anomalies.map((anomaly) => (
                <article className="anomaly" key={`${anomaly.quote_id}-${anomaly.metric}`}>
                  <span className={`severity severity-${anomaly.severity}`}>{anomaly.severity}</span>
                  <div><b>{anomaly.metric.replaceAll("_", " ")}</b><p>{anomaly.explanation}</p></div>
                </article>
              )) : <p className="muted-copy">No supplier-history anomaly was recorded for this run.</p>
            )}
          </div>
          <div className="audit-panel">
            <div className="panel-title"><span>Audit trail</span><span>{surfacedEvents.length} events</span></div>
            <ol className="audit-list">
              {(surfacedEvents.length ? surfacedEvents : detail?.audit_events ?? []).slice(-5).map((event) => (
                <li key={`${event.sequence}-${event.event_type}`}>
                  <span className="audit-number">{event.sequence}</span>
                  <div><b>{eventLabel(event)}</b><p>{event.reason}</p></div>
                  <time dateTime={event.occurred_at}>{dateTime(event.occurred_at)}</time>
                </li>
              ))}
              {!detail && <li className="empty-event"><span className="audit-number">—</span><div><b>Waiting for a run</b><p>Persisted RFQ events will appear here.</p></div></li>}
            </ol>
          </div>
        </div>
      </section>

      <section className="decision-section" id="decision" aria-labelledby="decision-title">
        <div className="wrap section-heading">
          <h2 id="decision-title">Approve a draft, never an order</h2>
          <p>The API verifies the buyer, quote, budget, and RFQ state before it creates a non-binding payload.</p>
        </div>
        <div className="wrap decision-grid">
          <div className="decision-panel">
            <div className="panel-title"><span>Approval card</span><span className="draft-pill">draft-only</span></div>
            {!detail ? <p className="muted-copy">Load an RFQ to open its buyer review.</p> : (
              <>
                <label htmlFor="buyer-id">Prototype buyer ID</label>
                <input id="buyer-id" value={buyerId} onChange={(event) => setBuyerId(event.target.value)} autoComplete="off" />
                <label htmlFor="quote-choice">Eligible quote</label>
                <select id="quote-choice" value={selectedQuoteId} onChange={(event) => setSelectedQuoteId(event.target.value)} disabled={detail.run.state !== "awaiting_approval"}>
                  <option value="">Select an eligible quote</option>
                  {eligibleScores.map((score) => {
                    const quote = quotes.find((candidate) => candidate.quote_id === score.quote_id);
                    return <option key={score.quote_id} value={score.quote_id}>{quote?.vendor_name ?? score.vendor_id} · {money(score.landed_cost.total_landed_cost, currency)}</option>;
                  })}
                </select>
                <label htmlFor="decision-note">Decision note <span>optional</span></label>
                <textarea id="decision-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Why this draft is ready for buyer review" rows={3} disabled={detail.run.state !== "awaiting_approval"} />
                {detail.run.state === "negotiation_or_ready" ? (
                  <button className="button button-primary full-button" type="button" onClick={() => void requestApproval()} disabled={working || !eligibleScores.length}>Open buyer review</button>
                ) : detail.run.state === "awaiting_approval" ? (
                  <div className="decision-actions">
                    <button className="button button-primary" type="button" onClick={() => void submitDecision("approve")} disabled={working || !selectedQuoteId}>Approve PO draft</button>
                    <button className="button button-danger" type="button" onClick={() => void submitDecision("reject")} disabled={working}>Reject RFQ outcome</button>
                  </div>
                ) : <DecisionOutcome detail={detail} currency={currency} />}
              </>
            )}
          </div>
          <div className="counter-panel">
            <div className="panel-title"><span>Counter-offer</span><span>confidential</span></div>
            <p>Creates reviewable fixture prose using only a recipient and anonymized target. It never sends an email.</p>
            <div className="counter-summary">
              <span>Selected supplier</span>
              <strong>{selectedVendor?.vendor_name ?? "Choose an RFQ quote"}</strong>
              <span>Target</span>
              <strong>{selectedScore ? money(Number(selectedScore.landed_cost.total_landed_cost) * 0.95, currency) : "—"}</strong>
            </div>
            <button className="button button-secondary full-button" type="button" onClick={() => void prepareCounterOffer()} disabled={working || detail?.run.state !== "negotiation_or_ready" || !selectedQuoteId}>Prepare counter-offer draft</button>
            {counterOffer && <article className="draft-preview"><span className="draft-label">Draft subject</span><b>{counterOffer.subject}</b><p>{counterOffer.body}</p><small>Disclosure check: {counterOffer.disclosure_check}</small></article>}
          </div>
        </div>
      </section>

      <footer className="site-footer">
        <div className="wrap footer-grid">
          <div><span className="brand-mark" aria-hidden="true">Q</span><strong>QuoteHawk</strong></div>
          <p>Local buyer experience for a draft-only procurement prototype.</p>
          <a href="#load-run">Load RFQ run</a>
        </div>
      </footer>
    </main>
  );
}

function EmptyComparison() {
  return (
    <div className="empty-comparison">
      <div className="empty-grid" aria-hidden="true"><span /><span /><span /><span /><span /></div>
      <div><b>No RFQ loaded</b><p>Start with a persisted run or create the local five-bid fixture.</p></div>
    </div>
  );
}

function CostBreakdown({ score, currency }: { score: QuoteScore; currency: string }) {
  const cost = score.landed_cost;
  return (
    <aside className="cost-breakdown" aria-label="Best landed-cost breakdown">
      <div><span>Selected view</span><strong>{score.quote_id}</strong></div>
      <div><span>Lines</span><strong>{money(cost.line_total, currency)}</strong></div>
      <div><span>Freight + shipping + duties</span><strong>{money(Number(cost.freight_cost) + Number(cost.shipping_cost) + Number(cost.duties_cost), currency)}</strong></div>
      <div><span>Policy adjustments</span><strong>{money(Number(cost.lead_time_penalty) + Number(cost.minimum_order_penalty) + Number(cost.payment_term_penalty) + Number(cost.quality_penalty) - Number(cost.early_payment_discount), currency)}</strong></div>
      <div className="cost-total"><span>Total landed cost</span><strong>{money(cost.total_landed_cost, currency)}</strong></div>
    </aside>
  );
}

function DecisionOutcome({ detail, currency }: { detail: RfqRunDetail; currency: string }) {
  if (detail.run.state === "approved_draft" && detail.purchase_order_draft) {
    return <div className="decision-outcome approved"><b>PO draft prepared</b><p>{detail.purchase_order_draft.vendor_name} · {money(detail.purchase_order_draft.total_landed_cost, currency)}</p><small>{detail.purchase_order_draft.disclaimer}</small></div>;
  }
  if (detail.run.state === "rejected") {
    return <div className="decision-outcome rejected"><b>RFQ outcome rejected</b><p>The buyer recorded a rejection. No PO draft exists.</p></div>;
  }
  return <div className="decision-outcome"><b>Approval is not available</b><p>This RFQ must complete its deterministic workflow first.</p></div>;
}
