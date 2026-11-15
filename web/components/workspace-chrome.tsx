export function WorkspaceHeader() {
  return (
    <>
      <a className="skip-link" href="#main-content">Skip to RFQ workspace</a>
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
    </>
  );
}

export function WorkspaceHero() {
  return (
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
  );
}

export function WorkspaceFooter() {
  return (
    <footer className="site-footer">
      <div className="wrap footer-grid">
        <div><span className="brand-mark" aria-hidden="true">Q</span><strong>QuoteHawk</strong></div>
        <p>Local buyer experience for a draft-only procurement prototype.</p>
        <a href="#load-run">Load RFQ run</a>
      </div>
    </footer>
  );
}
