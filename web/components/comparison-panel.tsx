"use client";

import { useEffect, useState } from "react";

import type { QuoteScore, RfqRunDetail } from "@/lib/types";
import { formatMoney, stateLabel, sumCosts } from "@/lib/format";
import { PanelSkeleton } from "@/components/workspace-skeleton";

const BIDS_PER_PAGE = 5;

type ComparisonPanelProps = {
  detail: RfqRunDetail | null;
  selectedQuoteId: string;
  isLoading: boolean;
  onSelectQuote: (quoteId: string) => void;
};

export function ComparisonPanel({
  detail,
  selectedQuoteId,
  isLoading,
  onSelectQuote,
}: ComparisonPanelProps) {
  const comparison = detail?.payload.comparison;
  const quotes = detail?.payload.quotes ?? [];
  const scores = comparison?.ranked_quotes ?? [];
  const currency = comparison?.currency ?? "USD";
  const selectedScore = scores.find((score) => score.quote_id === selectedQuoteId);
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(scores.length / BIDS_PER_PAGE));
  const visiblePage = Math.min(page, pageCount);
  const startIndex = (visiblePage - 1) * BIDS_PER_PAGE;
  const pageScores = scores.slice(startIndex, startIndex + BIDS_PER_PAGE);

  useEffect(() => {
    setPage(1);
  }, [scores.length]);

  return (
    <section className="workspace-section" id="comparison" aria-labelledby="comparison-title">
      <div className="wrap section-heading">
        <h2 id="comparison-title">Landed cost before preference</h2>
        <p>Each supplier remains visible. Only a bid inside the hard budget can move to buyer review.</p>
      </div>
      <div className="wrap" aria-busy={isLoading}>
        {!detail ? (
          isLoading ? <PanelSkeleton label="Loading supplier comparison" /> : <EmptyComparison />
        ) : (
          <>
            <RunSummary detail={detail} currency={currency} />
            <div className="comparison-table-scroll">
              <table className="comparison-table">
                <caption>Select a supplier quote to inspect its landed-cost breakdown.</caption>
                <thead>
                  <tr>
                    <th scope="col">Supplier</th>
                    <th scope="col">Landed cost</th>
                    <th scope="col">Score</th>
                    <th scope="col">Lead time</th>
                    <th scope="col">Approval</th>
                  </tr>
                </thead>
                <tbody>
                  {pageScores.map((score) => {
                    const quote = quotes.find((candidate) => candidate.quote_id === score.quote_id);
                    const selected = selectedQuoteId === score.quote_id;
                    const policyMessageId = `quote-policy-${score.quote_id}`;
                    return (
                      <tr className={selected ? "is-selected" : ""} key={score.quote_id}>
                        <th scope="row">
                          <label className="quote-selector">
                            <input
                              type="radio"
                              name="selected-quote"
                              value={score.quote_id}
                              checked={selected}
                              onChange={() => onSelectQuote(score.quote_id)}
                              aria-describedby={policyMessageId}
                            />
                            <span>
                              <b>{quote?.vendor_name ?? score.vendor_id}</b>
                              <small>{score.quote_id}</small>
                            </span>
                          </label>
                          <span className="sr-only" id={policyMessageId}>
                            {score.approval_ready
                              ? "Eligible for buyer approval."
                              : score.guardrail_messages.join(" ") || "Not eligible for buyer approval."}
                          </span>
                        </th>
                        <td>{formatMoney(score.landed_cost.total_landed_cost, currency)}</td>
                        <td>{Number(score.weighted_score).toFixed(1)}</td>
                        <td>{quote?.lead_time_days ?? "—"} days</td>
                        <td><span className={`tag ${score.approval_ready ? "tag-ready" : "tag-blocked"}`}>{score.approval_ready ? "Eligible" : "Blocked"}</span></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {scores.length > BIDS_PER_PAGE && (
              <nav className="comparison-pagination" aria-label="Supplier comparison pages">
                <button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={visiblePage === 1}>Previous five bids</button>
                <span aria-live="polite">Bids {startIndex + 1}–{Math.min(startIndex + BIDS_PER_PAGE, scores.length)} of {scores.length}</span>
                <button type="button" onClick={() => setPage((current) => Math.min(pageCount, current + 1))} disabled={visiblePage === pageCount}>Next five bids</button>
              </nav>
            )}
            <p className="selection-summary" aria-live="polite">
              {selectedScore
                ? `${selectedScore.quote_id} selected for review. ${selectedScore.approval_ready ? "It is eligible for buyer approval." : "It remains blocked by policy."}`
                : "Select a supplier quote to inspect its policy facts."}
            </p>
            {selectedScore && <CostBreakdown score={selectedScore} currency={currency} />}
          </>
        )}
      </div>
    </section>
  );
}

function RunSummary({ detail, currency }: { detail: RfqRunDetail; currency: string }) {
  return (
    <div className="run-strip">
      <div><span>Run</span><strong>{detail.run.run_id}</strong></div>
      <div><span>Requisition</span><strong>{detail.run.requisition_id}</strong></div>
      <div><span>Budget ceiling</span><strong>{formatMoney(detail.payload.requisition.max_landed_cost, currency)}</strong></div>
      <div><span>Workflow state</span><strong className={`state state-${detail.run.state}`}>{stateLabel[detail.run.state]}</strong></div>
    </div>
  );
}

function EmptyComparison() {
  return (
    <div className="empty-comparison">
      <div className="empty-grid" aria-hidden="true"><span /><span /><span /><span /><span /></div>
      <div><b>No RFQ loaded</b><p>Start with a persisted run or create the local five-bid review fixture.</p></div>
    </div>
  );
}

function CostBreakdown({ score, currency }: { score: QuoteScore; currency: string }) {
  const cost = score.landed_cost;
  const transportCosts = sumCosts(cost.freight_cost, cost.shipping_cost, cost.duties_cost);
  const policyAdjustments = sumCosts(
    cost.lead_time_penalty,
    cost.minimum_order_penalty,
    cost.payment_term_penalty,
    cost.quality_penalty,
    -Number(cost.early_payment_discount),
  );

  return (
    <aside className="cost-breakdown" aria-label={`Landed-cost breakdown for ${score.quote_id}`}>
      <div><span>Selected view</span><strong>{score.quote_id}</strong><small>{cost.source_currency === currency ? "Quoted in PR currency" : `${cost.source_currency} × ${cost.fx_rate} FX`}</small></div>
      <div><span>Lines</span><strong>{formatMoney(cost.line_total, currency)}</strong></div>
      <div><span>Freight + shipping + duties</span><strong>{formatMoney(transportCosts, currency)}</strong></div>
      <div><span>Policy adjustments</span><strong>{formatMoney(policyAdjustments, currency)}</strong></div>
      <div className="cost-total"><span>Total landed cost</span><strong>{formatMoney(cost.total_landed_cost, currency)}</strong></div>
    </aside>
  );
}
