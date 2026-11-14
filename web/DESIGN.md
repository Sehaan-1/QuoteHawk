# QuoteHawk buyer workspace plan

**Kind:** software — a draft-only RFQ approval workspace for procurement buyers.

**Facts used:** QuoteHawk compares up to five supplier bids; landed-cost math and anomaly checks are deterministic; the workflow ends in a non-binding `draft_only` PO payload; a buyer must approve; no email, ERP write-back, or binding PO is exposed. The local prototype default buyer ID is `demo-buyer`.

**Voice:** technical-dry. Exact, not cold; buyer-facing, not supplier-facing; mostly 8–16 word sentences. House words: RFQ, bid, landed cost, draft, buyer. Never: vendor outreach claims, execution claims, invented savings or performance figures.

**Deal:** `collage` hero; `warm-black-gold` palette because a dark review surface with a restrained gold decision accent fits a controlled procurement/fintech workflow; Mona Sans + Geist Mono type pairing; `solid` controls; one `iris-opens` review-record moment; no WebGL or photographs. The design uses an actual RFQ product window rather than a generic dashboard.

**Primary action:** Load RFQ run.

**Screen plan:**
1. `hero` — the RFQ approval workspace and draft-only boundary; actual RFQ workspace UI; 1 screen; product UI visual and hero entrance.
2. `comparison` — one five-bid comparison matrix, landed-cost figures and approval eligibility; 1 screen; product UI visual.
3. `review` — anomalies and concise audit timeline, no model reasoning; 1 screen; event-flow diagram and one type-led moment.
4. `decision` — buyer decision card and non-binding PO-draft status; 1 screen; approval panel visual.
5. `close` — local prototype setup and API boundary; 1 screen; large type close.

No photographs are used: this is a software workspace whose subject is its live RFQ UI. No claims, customers, prices, or performance metrics are invented.
