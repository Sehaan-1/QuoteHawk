import { StatusBar } from "expo-status-bar";
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { getRun, openApproval, submitDecision } from "./src/api";
import type { QuoteScore, RfqRunDetail } from "./src/types";

const colors = {
  bg: "#0c0a08",
  surface: "#181512",
  border: "#3b342c",
  ink: "#fffaf2",
  muted: "#a99f93",
  accent: "#c9a45a",
  accentInk: "#0c0a08",
  good: "#bfe0a9",
  goodSurface: "#1f3a25",
  danger: "#ffc7be",
  dangerSurface: "#45221d",
};

const formatMoney = (value: string, currency: string) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 2 }).format(
    Number(value),
  );

export default function App() {
  const [runId, setRunId] = useState("");
  const [buyerId, setBuyerId] = useState("demo-buyer");
  const [detail, setDetail] = useState<RfqRunDetail | null>(null);
  const [selectedQuoteId, setSelectedQuoteId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Load an RFQ run to review an eligible bid.");

  const currency = detail?.payload.requisition.currency ?? "USD";
  const scores = detail?.payload.comparison.ranked_quotes ?? [];
  const eligible = useMemo(() => scores.filter((score) => score.approval_ready), [scores]);

  const quoteName = (score: QuoteScore) =>
    detail?.payload.quotes.find((quote) => quote.quote_id === score.quote_id)?.vendor_name ?? score.vendor_id;

  async function loadRun() {
    if (!runId.trim()) {
      Alert.alert("RFQ run ID required", "Enter the persisted run ID from the QuoteHawk API.");
      return;
    }
    setBusy(true);
    try {
      const result = await getRun(runId.trim());
      setDetail(result);
      setRunId(result.run.run_id);
      setSelectedQuoteId(result.payload.comparison.ranked_quotes.find((score) => score.approval_ready)?.quote_id ?? "");
      setMessage(`Loaded ${result.run.run_id}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The RFQ run could not be loaded.");
    } finally {
      setBusy(false);
    }
  }

  async function requestBuyerReview() {
    if (!detail) return;
    setBusy(true);
    try {
      await openApproval(detail.run.run_id);
      await loadRun();
      setMessage("Buyer review is open. Approval still creates a draft only.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Buyer review could not be opened.");
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!detail) return;
    if (decision === "approve" && !selectedQuoteId) {
      Alert.alert("Eligible quote required", "Select an in-budget quote before approving a draft.");
      return;
    }
    setBusy(true);
    try {
      await submitDecision(detail.run.run_id, buyerId.trim(), decision, selectedQuoteId);
      await loadRun();
      setMessage(decision === "approve" ? "Non-binding PO draft prepared." : "RFQ outcome rejected.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The buyer decision could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <View style={styles.mark}><Text style={styles.markText}>Q</Text></View>
          <View><Text style={styles.brand}>QuoteHawk</Text><Text style={styles.subBrand}>Buyer approval</Text></View>
        </View>

        <View style={styles.boundary}><Text style={styles.boundaryKicker}>DRAFT-ONLY PROTOTYPE</Text><Text style={styles.boundaryText}>This screen can record a buyer decision. It cannot email a supplier, issue an order, or write to an ERP.</Text></View>

        <View style={styles.card}>
          <Text style={styles.label}>RFQ run ID</Text>
          <TextInput value={runId} onChangeText={setRunId} placeholder="rfq-…" placeholderTextColor={colors.muted} autoCapitalize="none" style={styles.input} />
          <Pressable onPress={() => void loadRun()} disabled={busy} style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, busy && styles.disabled]}>
            {busy ? <ActivityIndicator color={colors.accentInk} /> : <Text style={styles.primaryButtonText}>Load RFQ run</Text>}
          </Pressable>
          <Text style={styles.message}>{message}</Text>
        </View>

        {detail && (
          <>
            <View style={styles.runMeta}>
              <View><Text style={styles.metaLabel}>REQUISITION</Text><Text style={styles.metaValue}>{detail.run.requisition_id}</Text></View>
              <View><Text style={styles.metaLabel}>STATE</Text><Text style={styles.metaValue}>{detail.run.state.replaceAll("_", " ")}</Text></View>
            </View>

            <View style={styles.card}>
              <Text style={styles.sectionTitle}>Eligible bids</Text>
              <Text style={styles.sectionCopy}>Only in-budget quotes can become non-binding PO drafts.</Text>
              {eligible.length ? eligible.map((score) => (
                <Pressable key={score.quote_id} onPress={() => setSelectedQuoteId(score.quote_id)} style={[styles.quote, selectedQuoteId === score.quote_id && styles.quoteSelected]}>
                  <View><Text style={styles.quoteName}>{quoteName(score)}</Text><Text style={styles.quoteDetail}>{score.quote_id} · score {Number(score.weighted_score).toFixed(1)}</Text></View>
                  <View style={styles.quoteCost}><Text style={styles.quoteMoney}>{formatMoney(score.landed_cost.total_landed_cost, currency)}</Text><Text style={styles.eligible}>ELIGIBLE</Text></View>
                </Pressable>
              )) : <Text style={styles.sectionCopy}>No in-budget quote is available for approval.</Text>}
            </View>

            {detail.run.state === "negotiation_or_ready" && (
              <Pressable onPress={() => void requestBuyerReview()} disabled={busy || !eligible.length} style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, busy && styles.disabled]}>
                <Text style={styles.primaryButtonText}>Open buyer review</Text>
              </Pressable>
            )}

            {detail.run.state === "awaiting_approval" && (
              <View style={styles.card}>
                <Text style={styles.sectionTitle}>Buyer decision</Text>
                <Text style={styles.label}>Prototype buyer ID</Text>
                <TextInput value={buyerId} onChangeText={setBuyerId} autoCapitalize="none" style={styles.input} />
                <Pressable onPress={() => void decide("approve")} disabled={busy || !selectedQuoteId} style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, busy && styles.disabled]}>
                  <Text style={styles.primaryButtonText}>Approve PO draft</Text>
                </Pressable>
                <Pressable onPress={() => void decide("reject")} disabled={busy} style={({ pressed }) => [styles.rejectButton, pressed && styles.pressed, busy && styles.disabled]}>
                  <Text style={styles.rejectButtonText}>Reject RFQ outcome</Text>
                </Pressable>
              </View>
            )}

            {detail.run.state === "approved_draft" && detail.purchase_order_draft && (
              <View style={[styles.card, styles.approvedCard]}>
                <Text style={styles.eligible}>PO DRAFT PREPARED</Text>
                <Text style={styles.sectionTitle}>{detail.purchase_order_draft.vendor_name}</Text>
                <Text style={styles.draftCost}>{formatMoney(detail.purchase_order_draft.total_landed_cost, currency)}</Text>
                <Text style={styles.sectionCopy}>{detail.purchase_order_draft.disclaimer}</Text>
              </View>
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, gap: 16, paddingBottom: 44 },
  header: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8, marginBottom: 4 },
  mark: { width: 34, height: 34, borderRadius: 10, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center" },
  markText: { color: colors.accentInk, fontWeight: "800", fontSize: 16 },
  brand: { color: colors.ink, fontWeight: "700", fontSize: 18 },
  subBrand: { color: colors.muted, fontSize: 12, marginTop: 1 },
  boundary: { borderWidth: 1, borderColor: "#594829", borderRadius: 12, backgroundColor: "#1b160f", padding: 15, gap: 6 },
  boundaryKicker: { color: colors.accent, fontSize: 10, letterSpacing: 1.1, fontWeight: "800" },
  boundaryText: { color: "#ded3c3", fontSize: 13, lineHeight: 19 },
  card: { borderWidth: 1, borderColor: colors.border, borderRadius: 14, padding: 18, backgroundColor: colors.surface, gap: 12 },
  label: { color: "#d9cfc2", fontSize: 13, fontWeight: "700", marginTop: 4 },
  input: { borderWidth: 1, borderColor: "#51483e", borderRadius: 9, color: colors.ink, paddingHorizontal: 13, paddingVertical: 12, backgroundColor: "#120f0c", fontSize: 15 },
  primaryButton: { minHeight: 48, borderRadius: 10, backgroundColor: colors.accent, alignItems: "center", justifyContent: "center", paddingHorizontal: 16, marginTop: 4 },
  primaryButtonText: { color: colors.accentInk, fontWeight: "800", fontSize: 15 },
  pressed: { transform: [{ translateY: 1 }], opacity: 0.86 },
  disabled: { opacity: 0.5 },
  message: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  runMeta: { flexDirection: "row", gap: 12, borderWidth: 1, borderColor: colors.border, borderRadius: 12, padding: 14, backgroundColor: "#120f0c" },
  metaLabel: { color: colors.muted, fontSize: 10, fontWeight: "700", letterSpacing: 0.7 },
  metaValue: { color: colors.ink, marginTop: 5, textTransform: "capitalize", fontSize: 13, fontWeight: "600" },
  sectionTitle: { color: colors.ink, fontSize: 20, fontWeight: "700", letterSpacing: -0.3 },
  sectionCopy: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  quote: { minHeight: 72, borderWidth: 1, borderColor: "#3d352d", borderRadius: 10, padding: 13, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, backgroundColor: "#14110e" },
  quoteSelected: { borderColor: colors.accent, backgroundColor: "#241c11" },
  quoteName: { color: colors.ink, fontWeight: "700", fontSize: 14 },
  quoteDetail: { color: colors.muted, marginTop: 4, fontSize: 11 },
  quoteCost: { alignItems: "flex-end", gap: 4 },
  quoteMoney: { color: colors.ink, fontWeight: "700", fontSize: 13 },
  eligible: { color: colors.good, fontSize: 10, letterSpacing: 0.6, fontWeight: "800" },
  rejectButton: { minHeight: 48, borderWidth: 1, borderColor: "#7b4a43", borderRadius: 10, alignItems: "center", justifyContent: "center", marginTop: 10 },
  rejectButtonText: { color: colors.danger, fontWeight: "800", fontSize: 15 },
  approvedCard: { borderColor: "#49694c", backgroundColor: colors.goodSurface },
  draftCost: { color: colors.ink, fontSize: 27, fontWeight: "700", letterSpacing: -0.7 },
});
