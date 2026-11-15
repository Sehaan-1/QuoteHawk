import { Pressable, Text, View } from "react-native";

import { formatMoney, formatState } from "../format";
import { styles } from "../theme";
import type { RfqRunDetail } from "../types";

type ReviewSummaryProps = {
  detail: RfqRunDetail;
  busy: boolean;
  onRefresh: () => void;
};

export function ReviewSummary({ detail, busy, onRefresh }: ReviewSummaryProps) {
  const comparison = detail.payload.comparison;
  const eligibleCount = comparison.ranked_quotes.filter((score) => score.approval_ready).length;
  const itemCount = detail.payload.requisition.items?.length ?? 0;

  return (
    <>
      <View style={styles.runMeta} accessibilityRole="summary">
        <View style={styles.metaCell}>
          <Text style={styles.metaLabel}>REQUISITION</Text>
          <Text style={styles.metaValue}>{detail.run.requisition_id}</Text>
        </View>
        <View style={styles.metaCell}>
          <Text style={styles.metaLabel}>STATE</Text>
          <Text style={styles.metaValue}>{formatState(detail.run.state)}</Text>
        </View>
        <View style={styles.metaCell}>
          <Text style={styles.metaLabel}>BUDGET CEILING</Text>
          <Text style={styles.metaValue}>
            {formatMoney(detail.payload.requisition.max_landed_cost, comparison.currency)}
          </Text>
        </View>
        <View style={styles.metaCell}>
          <Text style={styles.metaLabel}>ELIGIBLE BIDS</Text>
          <Text style={styles.metaValue}>{eligibleCount} of {comparison.ranked_quotes.length}</Text>
        </View>
      </View>
      <View style={styles.cardInset}>
        <View style={styles.row}>
          <View>
            <Text style={styles.label}>Buyer review snapshot</Text>
            <Text style={styles.sectionCopy}>
              {itemCount ? `${itemCount} requisition line${itemCount === 1 ? "" : "s"} · ` : ""}
              costs are normalized into {comparison.currency}.
            </Text>
          </View>
          <Pressable
            onPress={onRefresh}
            disabled={busy}
            accessibilityRole="button"
            accessibilityLabel="Refresh RFQ review"
            style={({ pressed }) => [styles.secondaryButton, pressed && styles.pressed, busy && styles.disabled]}
          >
            <Text style={styles.secondaryButtonText}>Refresh</Text>
          </Pressable>
        </View>
      </View>
    </>
  );
}
