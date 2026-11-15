"use client";

import { useEffect, useMemo, useState } from "react";

import { ComparisonPanel } from "@/components/comparison-panel";
import { DecisionWorkspace } from "@/components/decision-workspace";
import { ReviewRecord, type StreamStatus } from "@/components/review-record";
import { RunLoader } from "@/components/run-loader";
import { WorkspaceFooter, WorkspaceHeader, WorkspaceHero } from "@/components/workspace-chrome";
import {
  createDemoRun,
  decideApproval,
  draftCounterOffer,
  getRun,
  openApproval,
} from "@/lib/api";
import type { AuditEvent, CounterOfferDraft, RfqRunDetail } from "@/lib/types";

const initialMessage = "Load an RFQ run or create a local fixture to begin.";

type WorkspaceAction = "load" | "fixture" | "approval" | "decision" | "counter-offer" | null;

export function RfqWorkspace() {
  const [runId, setRunId] = useState("");
  const [detail, setDetail] = useState<RfqRunDetail | null>(null);
  const [streamEvents, setStreamEvents] = useState<AuditEvent[]>([]);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("idle");
  const [prototypeBuyerId, setPrototypeBuyerId] = useState("");
  const [selectedQuoteId, setSelectedQuoteId] = useState("");
  const [note, setNote] = useState("");
  const [counterOffer, setCounterOffer] = useState<CounterOfferDraft | null>(null);
  const [message, setMessage] = useState(initialMessage);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<WorkspaceAction>(null);

  const isWorking = action !== null;
  const isLoadingRun = action === "load" || action === "fixture";
  const scores = detail?.payload.comparison.ranked_quotes ?? [];
  const quotes = detail?.payload.quotes ?? [];
  const selectedScore = scores.find((score) => score.quote_id === selectedQuoteId);
  const selectedVendor = quotes.find((quote) => quote.quote_id === selectedQuoteId);
  const surfacedEvents = useMemo(
    () => [...streamEvents].sort((left, right) => left.sequence - right.sequence),
    [streamEvents],
  );

  useEffect(() => {
    if (!detail) return;

    const firstEligible = detail.payload.comparison.ranked_quotes.find(
      (score) => score.approval_ready,
    );
    const nextQuoteId = firstEligible?.quote_id ?? "";
    const nextVendorId = detail.payload.quotes.find(
      (quote) => quote.quote_id === nextQuoteId,
    )?.vendor_id;
    setSelectedQuoteId(nextQuoteId);
    setStreamEvents(detail.audit_events);
    setCounterOffer(
      nextVendorId ? detail.payload.counter_offer_drafts?.[nextVendorId] ?? null : null,
    );
  }, [detail]);

  useEffect(() => {
    const activeRunId = detail?.run.run_id;
    if (!activeRunId) {
      setStreamStatus("idle");
      return;
    }

    setStreamStatus("connecting");
    const eventSource = new EventSource(`/api/rfq/runs/${encodeURIComponent(activeRunId)}/events`);
    const onAudit = (event: Event) => {
      try {
        const auditEvent = JSON.parse((event as MessageEvent<string>).data) as AuditEvent;
        if (!isAuditEvent(auditEvent)) throw new Error("invalid audit event");
        setStreamEvents((current) =>
          current.some((candidate) => candidate.sequence === auditEvent.sequence)
            ? current
            : [...current, auditEvent],
        );
        setStreamStatus("available");
      } catch {
        eventSource.close();
        setStreamStatus("unavailable");
      }
    };

    const onCounterOffer = (event: Event) => {
      try {
        const draft = JSON.parse((event as MessageEvent<string>).data) as CounterOfferDraft;
        if (!isCounterOfferDraft(draft)) throw new Error("invalid counter-offer draft");
        setCounterOffer(draft);
      } catch {
        eventSource.close();
        setStreamStatus("unavailable");
      }
    };

    eventSource.addEventListener("rfq.audit", onAudit);
    eventSource.addEventListener("rfq.counter_offer", onCounterOffer);
    eventSource.addEventListener("rfq.complete", () => {
      eventSource.close();
      setStreamStatus("available");
    });
    eventSource.onerror = () => {
      eventSource.close();
      setStreamStatus("unavailable");
    };

    return () => eventSource.close();
  }, [detail?.run.run_id]);

  async function loadRun(nextRunId = runId) {
    const normalizedRunId = nextRunId.trim();
    if (!normalizedRunId) {
      setError("Enter an RFQ run ID first.");
      return;
    }

    setAction("load");
    setError(null);
    try {
      const result = await getRun(normalizedRunId);
      setDetail(result);
      setRunId(result.run.run_id);
      setMessage(`Loaded ${result.run.run_id}. All values below come from the persisted run.`);
    } catch (reason) {
      setError(messageFor(reason, "Could not load the RFQ run."));
    } finally {
      setAction(null);
    }
  }

  async function loadDemo() {
    const demoRunId = `demo-${Date.now()}`;
    setAction("fixture");
    setError(null);
    try {
      await createDemoRun(demoRunId);
      const result = await getRun(demoRunId);
      setDetail(result);
      setRunId(result.run.run_id);
      setMessage("Created a local five-bid review fixture. Nothing was sent to a supplier or ERP.");
    } catch (reason) {
      setError(messageFor(reason, "Could not create the fixture run."));
    } finally {
      setAction(null);
    }
  }

  async function requestApproval() {
    if (!detail) return;

    setAction("approval");
    setError(null);
    try {
      await openApproval(detail.run.run_id);
      const result = await getRun(detail.run.run_id);
      setDetail(result);
      setMessage("Buyer review is open. Selecting a quote still creates a non-binding draft only.");
    } catch (reason) {
      setError(messageFor(reason, "Could not open buyer review."));
    } finally {
      setAction(null);
    }
  }

  async function submitDecision(decision: "approve" | "override" | "reject") {
    if (!detail) return;
    if (decision !== "reject" && (!selectedScore || !selectedScore.approval_ready)) {
      setError("Select an eligible quote before creating a draft.");
      return;
    }
    if (decision === "override" && !note.trim()) {
      setError("Document why the eligible supplier selection needs an override.");
      return;
    }

    setAction("decision");
    setError(null);
    try {
      await decideApproval(
        detail.run.run_id,
        decision,
        prototypeBuyerId.trim() || undefined,
        selectedQuoteId,
        note.trim(),
      );
      const result = await getRun(detail.run.run_id);
      setDetail(result);
      setNote("");
      setMessage(
        decision === "approve"
          ? "The buyer decision created a non-binding PO draft. No order was issued."
          : decision === "override"
          ? "The documented override created an eligible non-binding PO draft. No order was issued."
          : "The buyer rejected this RFQ outcome. No draft was created.",
      );
    } catch (reason) {
      // A configured webhook can fail after the local draft-only decision is durable.
      // Refresh so the approval card reflects that authoritative state rather than
      // inviting a duplicate click.
      try {
        setDetail(await getRun(detail.run.run_id));
      } catch {
        // Preserve the original action error if a recovery read also fails.
      }
      setError(messageFor(reason, "Could not record the buyer decision."));
    } finally {
      setAction(null);
    }
  }

  async function prepareCounterOffer() {
    if (!detail || !selectedVendor || !selectedScore) {
      setError("Choose a quote before preparing a counter-offer.");
      return;
    }

    const target = (Number(selectedScore.landed_cost.total_landed_cost) * 0.95).toFixed(2);
    setAction("counter-offer");
    setError(null);
    try {
      const draft = await draftCounterOffer(detail.run.run_id, selectedVendor.vendor_id, target);
      setCounterOffer(draft);
      setMessage("Prepared a fixture counter-offer draft. QuoteHawk did not send it.");
    } catch (reason) {
      setError(messageFor(reason, "Could not prepare the counter-offer draft."));
    } finally {
      setAction(null);
    }
  }

  function selectQuote(quoteId: string) {
    setSelectedQuoteId(quoteId);
    const vendorId = detail?.payload.quotes.find((quote) => quote.quote_id === quoteId)?.vendor_id;
    setCounterOffer(vendorId ? detail?.payload.counter_offer_drafts?.[vendorId] ?? null : null);
  }

  return (
    <>
      <WorkspaceHeader />
      <main id="main-content" tabIndex={-1}>
        <WorkspaceHero />
        <RunLoader
          runId={runId}
          isWorking={isWorking}
          message={message}
          error={error}
          onRunIdChange={setRunId}
          onLoad={() => void loadRun()}
          onCreateFixture={() => void loadDemo()}
        />
        <ComparisonPanel
          detail={detail}
          selectedQuoteId={selectedQuoteId}
          isLoading={isLoadingRun}
          onSelectQuote={selectQuote}
        />
        <ReviewRecord
          detail={detail}
          events={surfacedEvents}
          isLoading={isLoadingRun}
          streamStatus={streamStatus}
        />
        <DecisionWorkspace
          detail={detail}
          selectedQuoteId={selectedQuoteId}
          prototypeBuyerId={prototypeBuyerId}
          note={note}
          counterOffer={counterOffer}
          isWorking={isWorking}
          isLoading={isLoadingRun}
          onSelectQuote={selectQuote}
          onPrototypeBuyerIdChange={setPrototypeBuyerId}
          onNoteChange={setNote}
          onOpenApproval={() => void requestApproval()}
          onDecision={(decision) => void submitDecision(decision)}
          onPrepareCounterOffer={() => void prepareCounterOffer()}
        />
      </main>
      <WorkspaceFooter />
    </>
  );
}

function isAuditEvent(value: unknown): value is AuditEvent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<AuditEvent>;
  return (
    typeof candidate.sequence === "number"
    && typeof candidate.occurred_at === "string"
    && typeof candidate.event_type === "string"
    && typeof candidate.reason === "string"
  );
}

function isCounterOfferDraft(value: unknown): value is CounterOfferDraft {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<CounterOfferDraft>;
  return (
    candidate.status === "draft_only"
    && typeof candidate.vendor_id === "string"
    && Array.isArray(candidate.thread)
  );
}

function messageFor(reason: unknown, fallback: string) {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}
