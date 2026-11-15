"use client";

import type { CounterOfferDraft, Quote, QuoteScore, RfqRunDetail } from "@/lib/types";
import { formatMoney } from "@/lib/format";
import { PanelSkeleton } from "@/components/workspace-skeleton";

type DecisionWorkspaceProps = {
  detail: RfqRunDetail | null;
  selectedQuoteId: string;
  prototypeBuyerId: string;
  note: string;
  counterOffer: CounterOfferDraft | null;
  isWorking: boolean;
  isLoading: boolean;
  onSelectQuote: (quoteId: string) => void;
  onPrototypeBuyerIdChange: (buyerId: string) => void;
  onNoteChange: (note: string) => void;
  onOpenApproval: () => void;
  onDecision: (decision: "approve" | "override" | "reject") => void;
  onPrepareCounterOffer: () => void;
};

export function DecisionWorkspace({
  detail,
  selectedQuoteId,
  prototypeBuyerId,
  note,
  counterOffer,
  isWorking,
  isLoading,
  onSelectQuote,
  onPrototypeBuyerIdChange,
  onNoteChange,
  onOpenApproval,
  onDecision,
  onPrepareCounterOffer,
}: DecisionWorkspaceProps) {
  const scores = detail?.payload.comparison.ranked_quotes ?? [];
  const quotes = detail?.payload.quotes ?? [];
  const currency = detail?.payload.comparison.currency ?? "USD";
  const eligibleScores = scores.filter((score) => score.approval_ready);
  const selectedScore = scores.find((score) => score.quote_id === selectedQuoteId);
  const selectedVendor = quotes.find((quote) => quote.quote_id === selectedQuoteId);

  return (
    <section className="decision-section" id="decision" aria-labelledby="decision-title">
      <div className="wrap section-heading">
        <h2 id="decision-title">Approve a draft, never an order</h2>
        <p>The API verifies the buyer, quote, budget, and RFQ state before it creates a non-binding payload.</p>
      </div>
      <div className="wrap decision-grid" aria-busy={isLoading || isWorking}>
        <ApprovalPanel
          detail={detail}
          currency={currency}
          eligibleScores={eligibleScores}
          quotes={quotes}
          selectedQuoteId={selectedQuoteId}
          prototypeBuyerId={prototypeBuyerId}
          note={note}
          isWorking={isWorking}
          isLoading={isLoading}
          onSelectQuote={onSelectQuote}
          onPrototypeBuyerIdChange={onPrototypeBuyerIdChange}
          onNoteChange={onNoteChange}
          onOpenApproval={onOpenApproval}
          onDecision={onDecision}
        />
        <CounterOfferPanel
          detail={detail}
          currency={currency}
          selectedScore={selectedScore}
          selectedVendor={selectedVendor}
          counterOffer={counterOffer}
          isWorking={isWorking}
          onPrepareCounterOffer={onPrepareCounterOffer}
        />
      </div>
    </section>
  );
}

type ApprovalPanelProps = Pick<
  DecisionWorkspaceProps,
  | "detail"
  | "selectedQuoteId"
  | "prototypeBuyerId"
  | "note"
  | "isWorking"
  | "isLoading"
  | "onSelectQuote"
  | "onPrototypeBuyerIdChange"
  | "onNoteChange"
  | "onOpenApproval"
  | "onDecision"
> & {
  currency: string;
  eligibleScores: QuoteScore[];
  quotes: Quote[];
};

function ApprovalPanel({
  detail,
  currency,
  eligibleScores,
  quotes,
  selectedQuoteId,
  prototypeBuyerId,
  note,
  isWorking,
  isLoading,
  onSelectQuote,
  onPrototypeBuyerIdChange,
  onNoteChange,
  onOpenApproval,
  onDecision,
}: ApprovalPanelProps) {
  return (
    <section className="decision-panel" aria-labelledby="approval-card-title">
      <div className="panel-title"><span id="approval-card-title">Approval card</span><span className="draft-pill">draft-only</span></div>
      {!detail && isLoading ? <PanelSkeleton label="Loading approval controls" /> : !detail ? (
        <p className="muted-copy">Load an RFQ to open its buyer review.</p>
      ) : (
        <>
          <fieldset className="identity-fieldset">
            <legend>Buyer identity</legend>
            <p className="field-hint" id="buyer-identity-help">
              In the local prototype, enter a server-allowlisted buyer ID. A verified OIDC session records its token subject instead; this field cannot grant a role or tenant.
            </p>
            <label htmlFor="buyer-id">Local prototype buyer ID</label>
            <input
              id="buyer-id"
              value={prototypeBuyerId}
              onChange={(event) => onPrototypeBuyerIdChange(event.target.value)}
              autoComplete="username"
              placeholder="Configured buyer ID"
              aria-describedby="buyer-identity-help"
              disabled={detail.run.state !== "awaiting_approval" || isWorking}
            />
          </fieldset>
          <label htmlFor="quote-choice">Eligible quote</label>
          <select id="quote-choice" value={selectedQuoteId} onChange={(event) => onSelectQuote(event.target.value)} disabled={detail.run.state !== "awaiting_approval" || isWorking}>
            <option value="">Select an eligible quote</option>
            {eligibleScores.map((score) => {
              const quote = quotes.find((candidate) => candidate.quote_id === score.quote_id);
              return <option key={score.quote_id} value={score.quote_id}>{quote?.vendor_name ?? score.vendor_id} · {formatMoney(score.landed_cost.total_landed_cost, currency)}</option>;
            })}
          </select>
          <label htmlFor="decision-note">Decision note <span>optional</span></label>
          <textarea id="decision-note" value={note} onChange={(event) => onNoteChange(event.target.value)} placeholder="Why this draft is ready for buyer review" rows={3} disabled={detail.run.state !== "awaiting_approval" || isWorking} />
          {detail.run.state === "negotiation_or_ready" ? (
            <button className="button button-primary full-button" type="button" onClick={onOpenApproval} disabled={isWorking || !eligibleScores.length}>Open buyer review</button>
          ) : detail.run.state === "awaiting_approval" ? (
            <>
              <div className="decision-actions">
                <button className="button button-primary" type="button" onClick={() => onDecision("approve")} disabled={isWorking || !eligibleScores.some((score) => score.quote_id === selectedQuoteId)}>Approve PO draft</button>
                <button className="button button-secondary" type="button" onClick={() => onDecision("override")} disabled={isWorking || !note.trim() || !eligibleScores.some((score) => score.quote_id === selectedQuoteId)}>Documented override</button>
                <button className="button button-danger" type="button" onClick={() => onDecision("reject")} disabled={isWorking}>Reject RFQ outcome</button>
              </div>
              <p className="field-hint">An override still requires an eligible in-budget quote and a documented reason. It can only create a draft-only PO and notify an optional buyer-decision webhook.</p>
            </>
          ) : <DecisionOutcome detail={detail} currency={currency} />}
        </>
      )}
    </section>
  );
}

type CounterOfferPanelProps = Pick<
  DecisionWorkspaceProps,
  "detail" | "counterOffer" | "isWorking" | "onPrepareCounterOffer"
> & {
  currency: string;
  selectedScore: QuoteScore | undefined;
  selectedVendor: Quote | undefined;
};

function CounterOfferPanel({
  detail,
  currency,
  selectedScore,
  selectedVendor,
  counterOffer,
  isWorking,
  onPrepareCounterOffer,
}: CounterOfferPanelProps) {
  const target = selectedScore
    ? formatMoney(Number(selectedScore.landed_cost.total_landed_cost) * 0.95, currency)
    : "—";

  return (
    <section className="counter-panel" aria-labelledby="counter-offer-title">
      <div className="panel-title"><span id="counter-offer-title">Counter-offer</span><span>confidential</span></div>
      <p>Creates reviewable fixture prose using only a recipient and anonymized target. It never sends an email.</p>
      <div className="counter-summary">
        <span>Selected supplier</span>
        <strong>{selectedVendor?.vendor_name ?? "Choose an RFQ quote"}</strong>
        <span>Target</span>
        <strong>{target}</strong>
      </div>
      <button className="button button-secondary full-button" type="button" onClick={onPrepareCounterOffer} disabled={isWorking || detail?.run.state !== "negotiation_or_ready" || !selectedScore}>Prepare counter-offer draft</button>
      {counterOffer && <NegotiationThread draft={counterOffer} />}
    </section>
  );
}

function NegotiationThread({ draft }: { draft: CounterOfferDraft }) {
  return (
    <section className="negotiation-thread" aria-labelledby="negotiation-thread-title">
      <div className="thread-heading">
        <span className="draft-label" id="negotiation-thread-title">Simulated negotiation thread</span>
        <span>{draft.thread.length} messages</span>
      </div>
      <p className="thread-boundary">Draft simulation only. No message was delivered to a supplier.</p>
      <ol>
        {draft.thread.map((message, index) => (
          <li className={`thread-message thread-message-${message.speaker}`} key={`${message.round_number}-${message.speaker}-${index}`}>
            <span>{message.speaker === "buyer" ? "Buyer draft" : "Supplier simulation"} · round {message.round_number}</span>
            <b>{message.subject}</b>
            <p>{message.body}</p>
          </li>
        ))}
      </ol>
      <small>Disclosure check: {draft.disclosure_check}</small>
    </section>
  );
}

function DecisionOutcome({ detail, currency }: { detail: RfqRunDetail; currency: string }) {
  if (detail.run.state === "approved_draft" && detail.purchase_order_draft) {
    return <div className="decision-outcome approved"><b>PO draft prepared</b><p>{detail.purchase_order_draft.vendor_name} · {formatMoney(detail.purchase_order_draft.total_landed_cost, currency)}</p><small>{detail.purchase_order_draft.disclaimer}</small></div>;
  }
  if (detail.run.state === "rejected") {
    return <div className="decision-outcome rejected"><b>RFQ outcome rejected</b><p>The buyer recorded a rejection. No PO draft exists.</p></div>;
  }
  return <div className="decision-outcome"><b>Approval is not available</b><p>This RFQ must complete its deterministic workflow first.</p></div>;
}
