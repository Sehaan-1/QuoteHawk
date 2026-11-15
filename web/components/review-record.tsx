"use client";

import { useEffect, useMemo, useState } from "react";

import { auditEventLabel, formatDateTime } from "@/lib/format";
import type { AuditEvent, RfqRunDetail } from "@/lib/types";
import { PanelSkeleton } from "@/components/workspace-skeleton";

type StreamStatus = "idle" | "connecting" | "available" | "unavailable";

type ReviewRecordProps = {
  detail: RfqRunDetail | null;
  events: AuditEvent[];
  isLoading: boolean;
  streamStatus: StreamStatus;
};

const EVENTS_PER_PAGE = 5;

export function ReviewRecord({ detail, events, isLoading, streamStatus }: ReviewRecordProps) {
  return (
    <section
      className="review-section"
      id="review"
      data-moment="iris-opens"
      data-moment-opts='{"from":"base"}'
      aria-labelledby="review-title"
    >
      <div className="wrap section-heading section-heading-light">
        <p className="eyebrow">Review record</p>
        <h2 id="review-title">Exceptions and audit events, without hidden reasoning</h2>
        <p>The workspace shows persisted calculations and policy facts. Model traces are never displayed.</p>
      </div>
      <div className="wrap review-grid" aria-busy={isLoading}>
        <AnomalyPanel detail={detail} isLoading={isLoading} />
        <AuditPanel detail={detail} events={events} isLoading={isLoading} streamStatus={streamStatus} />
      </div>
    </section>
  );
}

function AnomalyPanel({ detail, isLoading }: Pick<ReviewRecordProps, "detail" | "isLoading">) {
  const anomalies = detail?.payload.anomalies ?? [];

  return (
    <section className="anomaly-panel" aria-labelledby="anomaly-title">
      <div className="panel-title"><span id="anomaly-title">Supplier anomalies</span><span>{anomalies.length} flagged</span></div>
      {!detail && isLoading ? <PanelSkeleton label="Loading supplier anomalies" /> : !detail ? (
        <p className="muted-copy">Load a run to inspect explainable deviations from supplier history.</p>
      ) : anomalies.length ? anomalies.map((anomaly) => (
        <article className="anomaly" key={`${anomaly.quote_id}-${anomaly.metric}`}>
          <span className={`severity severity-${anomaly.severity}`}>{anomaly.severity}</span>
          <div><b>{anomaly.metric.replaceAll("_", " ")}</b><p>{anomaly.explanation}</p></div>
        </article>
      )) : <p className="muted-copy">No supplier-history anomaly was recorded for this run.</p>}
    </section>
  );
}

function AuditPanel({ detail, events, isLoading, streamStatus }: ReviewRecordProps) {
  const sortedEvents = useMemo(
    () => [...events].sort((left, right) => left.sequence - right.sequence),
    [events],
  );
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(sortedEvents.length / EVENTS_PER_PAGE));
  const visiblePage = Math.min(page, pageCount);
  const startIndex = (visiblePage - 1) * EVENTS_PER_PAGE;
  const pageEvents = sortedEvents.slice(startIndex, startIndex + EVENTS_PER_PAGE);

  useEffect(() => {
    setPage(1);
  }, [sortedEvents.length]);

  return (
    <section className="audit-panel" aria-labelledby="audit-title">
      <div className="panel-title"><span id="audit-title">Audit trail</span><span>{sortedEvents.length} events</span></div>
      {!detail && isLoading ? <PanelSkeleton label="Loading RFQ audit trail" /> : (
        <>
          {detail && streamStatus === "connecting" && <p className="stream-note" role="status">Checking for live audit updates…</p>}
          {detail && streamStatus === "unavailable" && <p className="stream-note" role="status">Live updates are unavailable. Persisted audit facts are still shown.</p>}
          <ol className="audit-list" start={startIndex + 1}>
            {pageEvents.map((event) => <AuditEventItem event={event} key={`${event.sequence}-${event.event_type}`} />)}
            {!detail && <li className="empty-event"><span className="audit-number">—</span><div><b>Waiting for a run</b><p>Persisted RFQ events will appear here.</p></div></li>}
          </ol>
          {sortedEvents.length > EVENTS_PER_PAGE && (
            <nav className="pagination" aria-label="Audit event pages">
              <button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={visiblePage === 1}>Previous</button>
              <span aria-live="polite">Page {visiblePage} of {pageCount}</span>
              <button type="button" onClick={() => setPage((current) => Math.min(pageCount, current + 1))} disabled={visiblePage === pageCount}>Next</button>
            </nav>
          )}
        </>
      )}
    </section>
  );
}

function AuditEventItem({ event }: { event: AuditEvent }) {
  return (
    <li>
      <span className="audit-number">{event.sequence}</span>
      <div><b>{auditEventLabel(event)}</b><p>{event.reason}</p></div>
      <time dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
    </li>
  );
}

export type { StreamStatus };
