"use client";

export default function Error({ reset }: Readonly<{ error: Error & { digest?: string }; reset: () => void }>) {
  return (
    <main className="route-error" id="main-content" aria-labelledby="error-title">
      <section className="route-error-card">
        <p className="eyebrow">Buyer workspace</p>
        <h1 id="error-title">The RFQ workspace could not open</h1>
        <p>Reload the workspace to retry. No supplier message, ERP write, or purchase order was created.</p>
        <button className="button button-primary" type="button" onClick={reset}>Retry workspace</button>
      </section>
    </main>
  );
}
