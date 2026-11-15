"use client";

import "./globals.css";

export default function GlobalError({ reset }: Readonly<{ error: Error & { digest?: string }; reset: () => void }>) {
  return (
    <html lang="en">
      <body>
        <main className="route-error" aria-labelledby="global-error-title">
          <section className="route-error-card">
            <p className="eyebrow">QuoteHawk</p>
            <h1 id="global-error-title">The buyer workspace is unavailable</h1>
            <p>Try again to reload the draft-only workspace. No supplier or purchasing action was taken.</p>
            <button className="button button-primary" type="button" onClick={reset}>Retry workspace</button>
          </section>
        </main>
      </body>
    </html>
  );
}
