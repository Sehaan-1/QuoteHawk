import { useEffect, useState } from "react";
import { Pressable, Text, View } from "react-native";

import { formatMoney } from "../format";
import { styles } from "../theme";
import type { Quote, QuoteScore } from "../types";

type BidListProps = {
  scores: QuoteScore[];
  quotes: Quote[];
  currency: string;
  selectedQuoteId: string;
  onSelectQuote: (quoteId: string) => void;
};

const BIDS_PER_PAGE = 5;

export function BidList({
  scores,
  quotes,
  currency,
  selectedQuoteId,
  onSelectQuote,
}: BidListProps) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(scores.length / BIDS_PER_PAGE));
  const selectedIndex = scores.findIndex((score) => score.quote_id === selectedQuoteId);
  const visibleScores = scores.slice(page * BIDS_PER_PAGE, (page + 1) * BIDS_PER_PAGE);
  const selected = scores.find((score) => score.quote_id === selectedQuoteId);

  useEffect(() => {
    if (selectedIndex >= 0) setPage(Math.floor(selectedIndex / BIDS_PER_PAGE));
  }, [selectedIndex]);

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);

  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>Supplier comparison</Text>
      <Text style={styles.sectionCopy}>
        Review every evaluated bid in pages of five. A blocked quote cannot be approved,
        overridden, or turned into a draft.
      </Text>
      {visibleScores.map((score) => {
        const quote = quotes.find((candidate) => candidate.quote_id === score.quote_id);
        const isSelected = score.quote_id === selectedQuoteId;
        return (
          <Pressable
            key={score.quote_id}
            onPress={() => onSelectQuote(score.quote_id)}
            accessibilityRole="button"
            accessibilityState={{ selected: isSelected, disabled: false }}
            accessibilityLabel={`${quote?.vendor_name ?? score.vendor_id}, ${score.approval_ready ? "eligible" : "blocked"}, ${formatMoney(score.landed_cost.total_landed_cost, currency)}`}
            style={({ pressed }) => [
              styles.quote,
              isSelected && styles.quoteSelected,
              !score.approval_ready && styles.quoteBlocked,
              pressed && styles.pressed,
            ]}
          >
            <View>
              <Text style={styles.quoteName}>{quote?.vendor_name ?? score.vendor_id}</Text>
              <Text style={styles.quoteDetail}>
                {score.quote_id} · {quote?.lead_time_days ?? "—"} day lead · score {Number(score.weighted_score).toFixed(1)}
              </Text>
            </View>
            <View style={styles.quoteCost}>
              <Text style={styles.quoteMoney}>{formatMoney(score.landed_cost.total_landed_cost, currency)}</Text>
              <Text style={score.approval_ready ? styles.eligible : styles.blocked}>
                {score.approval_ready ? "ELIGIBLE" : "BLOCKED"}
              </Text>
            </View>
          </Pressable>
        );
      })}
      {scores.length > BIDS_PER_PAGE && (
        <View style={styles.pager} accessibilityLabel={`Bid page ${page + 1} of ${pageCount}`}>
          <Pressable
            onPress={() => setPage((current) => Math.max(0, current - 1))}
            disabled={page === 0}
            accessibilityRole="button"
            accessibilityLabel="Previous five bids"
            style={({ pressed }) => [styles.pagerButton, pressed && styles.pressed, page === 0 && styles.disabled]}
          >
            <Text style={styles.pagerButtonText}>Previous</Text>
          </Pressable>
          <Text style={styles.pagerText}>Bids {page * BIDS_PER_PAGE + 1}–{Math.min((page + 1) * BIDS_PER_PAGE, scores.length)} of {scores.length}</Text>
          <Pressable
            onPress={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
            disabled={page === pageCount - 1}
            accessibilityRole="button"
            accessibilityLabel="Next five bids"
            style={({ pressed }) => [styles.pagerButton, pressed && styles.pressed, page === pageCount - 1 && styles.disabled]}
          >
            <Text style={styles.pagerButtonText}>Next</Text>
          </Pressable>
        </View>
      )}
      {selected ? <LandedCostBreakdown score={selected} currency={currency} /> : null}
    </View>
  );
}

function LandedCostBreakdown({ score, currency }: { score: QuoteScore; currency: string }) {
  const cost = score.landed_cost;
  const sameCurrency = cost.source_currency === currency;

  return (
    <View style={styles.breakdown} accessibilityLabel={`Landed cost breakdown for ${score.quote_id}`}>
      <View style={styles.row}>
        <Text style={styles.label}>Selected bid breakdown</Text>
        <Text style={styles.quoteDetail}>
          {sameCurrency ? "Quoted in PR currency" : `${cost.source_currency} × ${cost.fx_rate} FX`}
        </Text>
      </View>
      <CostRow label="Quoted lines" value={cost.line_total} currency={currency} />
      <CostRow label="Freight" value={cost.freight_cost} currency={currency} />
      <CostRow label="Shipping" value={cost.shipping_cost} currency={currency} />
      <CostRow label="Duties" value={cost.duties_cost} currency={currency} />
      <CostRow label="Lead-time adjustment" value={cost.lead_time_penalty} currency={currency} />
      <CostRow label="MOQ adjustment" value={cost.minimum_order_penalty} currency={currency} />
      <CostRow label="Payment adjustment" value={cost.payment_term_penalty} currency={currency} />
      <CostRow label="Quality adjustment" value={cost.quality_penalty} currency={currency} />
      <CostRow label="Early-payment discount" value={cost.early_payment_discount} currency={currency} />
      <View style={styles.breakdownTotal}>
        <CostRow label="Total landed cost" value={cost.total_landed_cost} currency={currency} emphasis />
      </View>
      {!score.approval_ready && score.guardrail_messages.map((message) => (
        <Text key={message} style={styles.guardrail}>Policy: {message}</Text>
      ))}
    </View>
  );
}

function CostRow({
  label,
  value,
  currency,
  emphasis = false,
}: {
  label: string;
  value: string;
  currency: string;
  emphasis?: boolean;
}) {
  return (
    <View style={styles.breakdownRow}>
      <Text style={styles.breakdownLabel}>{label}</Text>
      <Text style={emphasis ? styles.draftCost : styles.breakdownValue}>
        {formatMoney(value, currency)}
      </Text>
    </View>
  );
}
