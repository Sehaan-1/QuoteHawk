import { StatusBar } from "expo-status-bar";
import { RefreshControl, SafeAreaView, ScrollView, View } from "react-native";
import { useState } from "react";

import { AppHeader } from "./src/components/AppHeader";
import { AuditTimeline } from "./src/components/AuditTimeline";
import { BidList } from "./src/components/BidList";
import { DecisionCard } from "./src/components/DecisionCard";
import { ReviewSummary } from "./src/components/ReviewSummary";
import { ReviewTabs } from "./src/components/ReviewTabs";
import { RunPicker } from "./src/components/RunPicker";
import { useRfqReview } from "./src/hooks/use-rfq-review";
import { styles } from "./src/theme";
import type { RfqView } from "./src/types";

export default function App() {
  const review = useRfqReview();
  const [view, setView] = useState<RfqView>("review");
  const comparison = review.detail?.payload.comparison;

  return (
    <SafeAreaView style={styles.screen}>
      <StatusBar style="light" />
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={review.operation === "refresh"}
            enabled={Boolean(review.detail)}
            onRefresh={() => { void review.load(undefined, "refresh"); }}
            tintColor="#c9a45a"
          />
        }
      >
        <AppHeader />
        <RunPicker
          runId={review.runId}
          recentRunIds={review.recentRunIds}
          busy={review.busy}
          notice={review.notice}
          onRunIdChange={review.setRunId}
          onLoad={(runId) => { void review.load(runId); }}
          onClearRecent={() => { void review.clearRecentRuns(); }}
        />
        {review.detail && comparison && (
          <>
            <ReviewSummary
              detail={review.detail}
              busy={review.busy}
              onRefresh={() => { void review.load(undefined, "refresh"); }}
            />
            <ReviewTabs value={view} onChange={setView} />
            {view === "review" ? (
              <View style={styles.stack}>
                <BidList
                  scores={comparison.ranked_quotes}
                  quotes={review.detail.payload.quotes}
                  currency={comparison.currency}
                  selectedQuoteId={review.selectedQuoteId}
                  onSelectQuote={review.setSelectedQuoteId}
                />
                <DecisionCard
                  detail={review.detail}
                  selectedQuoteId={review.selectedQuoteId}
                  buyerId={review.prototypeBuyerId}
                  note={review.note}
                  busy={review.busy}
                  onBuyerIdChange={review.setPrototypeBuyerId}
                  onNoteChange={review.setNote}
                  onOpenBuyerReview={() => { void review.openBuyerReview(); }}
                  onDecision={review.recordDecision}
                />
              </View>
            ) : (
              <AuditTimeline events={review.detail.audit_events} />
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}
