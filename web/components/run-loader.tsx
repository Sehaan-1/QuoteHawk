"use client";

import type { FormEvent } from "react";

type RunLoaderProps = {
  runId: string;
  isWorking: boolean;
  message: string;
  error: string | null;
  onRunIdChange: (runId: string) => void;
  onLoad: () => void;
  onCreateFixture: () => void;
};

export function RunLoader({
  runId,
  isWorking,
  message,
  error,
  onRunIdChange,
  onLoad,
  onCreateFixture,
}: RunLoaderProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onLoad();
  }

  const statusMessage = error ?? message;

  return (
    <section className="load-section" id="load-run" aria-labelledby="load-title">
      <div className="wrap load-grid">
        <div>
          <h2 id="load-title">Load a persisted RFQ</h2>
          <p>Enter a run ID from the API, or build a five-bid fixture in this local prototype.</p>
        </div>
        <form className="load-form" onSubmit={handleSubmit} aria-busy={isWorking}>
          <label htmlFor="run-id">RFQ run ID</label>
          <div className="input-row">
            <input
              id="run-id"
              value={runId}
              onChange={(event) => onRunIdChange(event.target.value)}
              placeholder="rfq-…"
              autoComplete="off"
              aria-describedby="run-helper"
              disabled={isWorking}
            />
            <button className="button button-primary" type="submit" disabled={isWorking}>
              {isWorking ? "Loading RFQ…" : "Load RFQ run"}
            </button>
          </div>
          <p className="field-hint" id="run-helper">A run ID selects persisted policy facts; it never submits a supplier action.</p>
          <button className="button button-secondary" type="button" onClick={onCreateFixture} disabled={isWorking}>
            {isWorking ? "Preparing fixture…" : "Create local fixture"}
          </button>
        </form>
      </div>
      <div
        className={`wrap status-row ${error ? "status-row-error" : ""}`}
        role={error ? "alert" : "status"}
        aria-live="polite"
        aria-atomic="true"
      >
        <span className="status-signal" aria-hidden="true" />
        <span>{statusMessage}</span>
      </div>
    </section>
  );
}
