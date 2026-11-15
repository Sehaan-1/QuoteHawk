type WorkspaceLoadingProps = {
  label?: string;
};

export function WorkspaceLoading({ label = "Loading RFQ workspace" }: WorkspaceLoadingProps) {
  return (
    <main id="main-content" className="workspace-loading" aria-busy="true" aria-live="polite">
      <div className="wrap loading-shell">
        <p className="eyebrow">Buyer workspace · draft-only</p>
        <p className="sr-only">{label}</p>
        <div className="skeleton skeleton-title" />
        <div className="skeleton skeleton-copy" />
        <div className="skeleton skeleton-copy skeleton-copy-short" />
        <div className="skeleton skeleton-action" />
      </div>
      <div className="wrap loading-grid" aria-hidden="true">
        <div className="skeleton skeleton-panel" />
        <div className="skeleton skeleton-panel" />
        <div className="skeleton skeleton-panel skeleton-panel-wide" />
      </div>
    </main>
  );
}

export function PanelSkeleton({ label }: { label: string }) {
  return (
    <div className="panel-skeleton" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      <div className="skeleton skeleton-panel-line" />
      <div className="skeleton skeleton-panel-line" />
      <div className="skeleton skeleton-panel-line skeleton-copy-short" />
    </div>
  );
}
