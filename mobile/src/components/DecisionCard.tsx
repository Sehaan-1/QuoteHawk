import { useMemo } from "react";
import {
  ActivityIndicator,
  Alert,
  PanResponder,
  Pressable,
  Text,
  TextInput,
  View,
} from "react-native";

import { formatMoney } from "../format";
import { colors, styles } from "../theme";
import type { BuyerDecision, Quote, RfqRunDetail } from "../types";

type DecisionCardProps = {
  detail: RfqRunDetail;
  selectedQuoteId: string;
  buyerId: string;
  note: string;
  busy: boolean;
  onBuyerIdChange: (value: string) => void;
  onNoteChange: (value: string) => void;
  onOpenBuyerReview: () => void;
  onDecision: (decision: BuyerDecision) => Promise<boolean>;
};

export function DecisionCard({
  detail,
  selectedQuoteId,
  buyerId,
  note,
  busy,
  onBuyerIdChange,
  onNoteChange,
  onOpenBuyerReview,
  onDecision,
}: DecisionCardProps) {
  const selectedQuote = detail.payload.quotes.find((quote) => quote.quote_id === selectedQuoteId);
  const selectedScore = detail.payload.comparison.ranked_quotes.find(
    (score) => score.quote_id === selectedQuoteId,
  );
  const canApprove = Boolean(selectedScore?.approval_ready && selectedQuoteId);
  const quoteLabel = quoteName(selectedQuote, selectedScore?.vendor_id);
  const currency = detail.payload.comparison.currency;

  const confirmDecision = (decision: BuyerDecision) => {
    const copy = decisionCopy(decision, quoteLabel, note.trim());
    Alert.alert(copy.title, copy.message, [
      { text: "Keep reviewing", style: "cancel" },
      {
        text: copy.action,
        style: decision === "reject" ? "destructive" : "default",
        onPress: () => { void onDecision(decision); },
      },
    ]);
  };

  const approvePanResponder = useMemo(
    () => PanResponder.create({
      onMoveShouldSetPanResponder: (_event, gesture) => !busy && canApprove && gesture.dx > 14,
      onPanResponderRelease: (_event, gesture) => {
        if (gesture.dx >= 88) confirmDecision("approve");
      },
    }),
    [busy, canApprove, note, quoteLabel],
  );

  if (detail.run.state === "negotiation_or_ready") {
    return (
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Open buyer review</Text>
        <Text style={styles.sectionCopy}>
          {canApprove
            ? `${quoteLabel} is selected from the in-budget options. Opening review does not send a message or issue a PO.`
            : "No in-budget bid is available. A buyer approval gate cannot open."}
        </Text>
        <Pressable
          onPress={onOpenBuyerReview}
          disabled={busy || !canApprove}
          accessibilityRole="button"
          style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, (busy || !canApprove) && styles.disabled]}
        >
          {busy ? <ActivityIndicator color={colors.accentInk} /> : <Text style={styles.primaryButtonText}>Open buyer review</Text>}
        </Pressable>
      </View>
    );
  }

  if (detail.run.state === "awaiting_approval") {
    return (
      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Buyer decision</Text>
        <Text style={styles.sectionCopy}>
          A signed session records its server-derived actor. The local prototype requires an
          allowlisted buyer ID. A configured webhook receives an audit notification only.
        </Text>
        <View style={styles.cardInset}>
          <Text style={styles.metaLabel}>SELECTED ELIGIBLE BID</Text>
          <Text style={styles.quoteName}>{quoteLabel}</Text>
          {selectedScore && (
            <Text style={styles.quoteDetail}>
              {formatMoney(selectedScore.landed_cost.total_landed_cost, currency)} · never a binding order
            </Text>
          )}
        </View>
        <Text style={styles.label}>Local prototype buyer ID</Text>
        <TextInput
          value={buyerId}
          onChangeText={onBuyerIdChange}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="Configured buyer ID"
          placeholderTextColor={colors.muted}
          editable={!busy}
          style={styles.input}
        />
        <Text style={styles.label}>Decision note or override reason</Text>
        <TextInput
          value={note}
          onChangeText={onNoteChange}
          multiline
          placeholder="Required for an override"
          placeholderTextColor={colors.muted}
          editable={!busy}
          style={[styles.input, styles.noteInput]}
        />
        <Pressable
          {...approvePanResponder.panHandlers}
          onPress={() => confirmDecision("approve")}
          disabled={busy || !canApprove}
          accessibilityRole="button"
          accessibilityHint="Swipe right on this control or double tap to confirm a non-binding approval."
          style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, (busy || !canApprove) && styles.disabled]}
        >
          {busy ? <ActivityIndicator color={colors.accentInk} /> : <Text style={styles.primaryButtonText}>Swipe right or tap to approve draft</Text>}
        </Pressable>
        <Pressable
          onPress={() => confirmDecision("override")}
          disabled={busy || !canApprove || !note.trim()}
          accessibilityRole="button"
          style={({ pressed }) => [styles.overrideButton, pressed && styles.pressed, (busy || !canApprove || !note.trim()) && styles.disabled]}
        >
          <Text style={styles.overrideButtonText}>Documented eligible-bid override</Text>
        </Pressable>
        <Pressable
          onPress={() => confirmDecision("reject")}
          disabled={busy}
          accessibilityRole="button"
          style={({ pressed }) => [styles.rejectButton, pressed && styles.pressed, busy && styles.disabled]}
        >
          <Text style={styles.rejectButtonText}>Reject RFQ outcome</Text>
        </Pressable>
      </View>
    );
  }

  if (detail.run.state === "approved_draft" && detail.purchase_order_draft) {
    return (
      <View style={[styles.card, styles.approvedCard]}>
        <Text style={styles.eligible}>PO DRAFT PREPARED</Text>
        <Text style={styles.sectionTitle}>{detail.purchase_order_draft.vendor_name}</Text>
        <Text style={styles.draftCost}>
          {formatMoney(detail.purchase_order_draft.total_landed_cost, currency)}
        </Text>
        <Text style={styles.sectionCopy}>{detail.purchase_order_draft.disclaimer}</Text>
      </View>
    );
  }

  if (detail.run.state === "rejected") {
    return (
      <View style={[styles.card, styles.rejectedCard]}>
        <Text style={styles.blocked}>RFQ OUTCOME REJECTED</Text>
        <Text style={styles.sectionTitle}>No PO draft exists</Text>
        <Text style={styles.sectionCopy}>
          The rejection is terminal for this buyer-review request. No supplier communication or ERP action occurred.
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>Decision unavailable</Text>
      <Text style={styles.sectionCopy}>
        QuoteHawk must complete deterministic comparison and policy checks before a buyer action is available.
      </Text>
    </View>
  );
}

function quoteName(quote: Quote | undefined, fallbackVendorId: string | undefined) {
  return quote?.vendor_name ?? fallbackVendorId ?? "Select an eligible quote";
}

function decisionCopy(decision: BuyerDecision, vendor: string, note: string) {
  if (decision === "approve") {
    return {
      title: "Prepare non-binding PO draft?",
      message: `${vendor} will be recorded as the selected eligible supplier. This does not send a supplier message, create an ERP order, or purchase goods.`,
      action: "Approve draft",
    };
  }
  if (decision === "override") {
    return {
      title: "Record documented override?",
      message: `${vendor} remains subject to the hard budget guardrail. Reason: ${note}. This creates only a non-binding draft.`,
      action: "Record override",
    };
  }
  return {
    title: "Reject this RFQ outcome?",
    message: "No PO draft will be created. No supplier message, ERP order, or purchase action will occur.",
    action: "Reject outcome",
  };
}
