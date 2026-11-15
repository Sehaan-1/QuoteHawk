import { useCallback, useEffect, useState } from "react";

import { getRun, openApproval, QuoteHawkApiError, submitDecision } from "../api";
import { clearRecentRunIds, loadRecentRunIds, rememberRunId } from "../recent-runs";
import type { BuyerDecision, RfqRunDetail } from "../types";

export type NoticeTone = "neutral" | "success" | "error";

type Notice = { tone: NoticeTone; text: string };
type Operation = "load" | "refresh" | "open-review" | "decision" | null;

const initialNotice: Notice = {
  tone: "neutral",
  text: "Load an RFQ run to review its in-budget supplier options.",
};

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function firstEligibleQuoteId(detail: RfqRunDetail) {
  return detail.payload.comparison.ranked_quotes.find((score) => score.approval_ready)?.quote_id ?? "";
}

export function useRfqReview() {
  const [runId, setRunId] = useState("");
  const [prototypeBuyerId, setPrototypeBuyerId] = useState("");
  const [detail, setDetail] = useState<RfqRunDetail | null>(null);
  const [selectedQuoteId, setSelectedQuoteId] = useState("");
  const [note, setNote] = useState("");
  const [recentRunIds, setRecentRunIds] = useState<string[]>([]);
  const [operation, setOperation] = useState<Operation>(null);
  const [notice, setNotice] = useState<Notice>(initialNotice);

  const busy = operation !== null;

  useEffect(() => {
    void loadRecentRunIds().then(setRecentRunIds);
  }, []);

  const applyDetail = useCallback((nextDetail: RfqRunDetail) => {
    setDetail(nextDetail);
    setRunId(nextDetail.run.run_id);
    setSelectedQuoteId((current) => {
      const existsAndEligible = nextDetail.payload.comparison.ranked_quotes.some(
        (score) => score.quote_id === current && score.approval_ready,
      );
      return existsAndEligible ? current : firstEligibleQuoteId(nextDetail);
    });
  }, []);

  const load = useCallback(async (nextRunId = runId, kind: "load" | "refresh" = "load") => {
    const normalizedRunId = nextRunId.trim();
    if (!normalizedRunId) {
      setNotice({ tone: "error", text: "Enter a persisted RFQ run ID first." });
      return false;
    }
    setOperation(kind);
    setNotice(initialNotice);
    try {
      const result = await getRun(normalizedRunId);
      applyDetail(result);
      setRecentRunIds(await rememberRunId(result.run.run_id));
      setNotice({ tone: "success", text: `Loaded ${result.run.run_id}.` });
      return true;
    } catch (error) {
      setNotice({ tone: "error", text: messageFor(error, "The RFQ run could not be loaded.") });
      return false;
    } finally {
      setOperation(null);
    }
  }, [applyDetail, runId]);

  const openBuyerReview = useCallback(async () => {
    if (!detail) return;
    setOperation("open-review");
    setNotice(initialNotice);
    try {
      await openApproval(detail.run.run_id);
      const result = await getRun(detail.run.run_id);
      applyDetail(result);
      setNotice({
        tone: "success",
        text: "Buyer review is open. Any decision still creates a non-binding draft only.",
      });
    } catch (error) {
      setNotice({ tone: "error", text: messageFor(error, "Buyer review could not be opened.") });
    } finally {
      setOperation(null);
    }
  }, [applyDetail, detail]);

  const recordDecision = useCallback(async (decision: BuyerDecision) => {
    if (!detail) return false;
    const selectedIsEligible = detail.payload.comparison.ranked_quotes.some(
      (score) => score.quote_id === selectedQuoteId && score.approval_ready,
    );
    if (decision !== "reject" && !selectedIsEligible) {
      setNotice({ tone: "error", text: "Select an eligible quote before creating a draft." });
      return false;
    }
    if (decision === "override" && !note.trim()) {
      setNotice({ tone: "error", text: "Document why the eligible supplier selection needs an override." });
      return false;
    }
    setOperation("decision");
    setNotice(initialNotice);
    try {
      await submitDecision(
        detail.run.run_id,
        prototypeBuyerId.trim() || undefined,
        decision,
        selectedQuoteId,
        note.trim() || undefined,
      );
      const result = await getRun(detail.run.run_id);
      applyDetail(result);
      setNote("");
      setNotice({
        tone: "success",
        text: decision === "approve"
          ? "Non-binding PO draft prepared. No supplier, ERP, or purchase action occurred."
          : decision === "override"
          ? "Documented eligible-bid override recorded as a non-binding PO draft."
          : "RFQ outcome rejected. No PO draft was created.",
      });
      return true;
    } catch (error) {
      // A webhook error can happen after a local draft-only decision is durable.
      // Reload once so the buyer never receives a stale card that invites a repeat.
      if (error instanceof QuoteHawkApiError && error.status === 502) {
        try {
          applyDetail(await getRun(detail.run.run_id));
        } catch {
          // Preserve the original notification failure if recovery is unavailable.
        }
      }
      setNotice({ tone: "error", text: messageFor(error, "The buyer decision could not be saved.") });
      return false;
    } finally {
      setOperation(null);
    }
  }, [applyDetail, detail, note, prototypeBuyerId, selectedQuoteId]);

  const clearRecentRuns = useCallback(async () => {
    await clearRecentRunIds();
    setRecentRunIds([]);
  }, []);

  return {
    busy,
    detail,
    notice,
    note,
    operation,
    prototypeBuyerId,
    recentRunIds,
    runId,
    selectedQuoteId,
    clearRecentRuns,
    load,
    openBuyerReview,
    recordDecision,
    setNote,
    setPrototypeBuyerId,
    setRunId,
    setSelectedQuoteId,
  };
}
