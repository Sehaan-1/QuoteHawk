import { ActivityIndicator, Pressable, Text, TextInput, View } from "react-native";

import type { NoticeTone } from "../hooks/use-rfq-review";
import { colors, styles } from "../theme";

type RunPickerProps = {
  runId: string;
  recentRunIds: string[];
  busy: boolean;
  notice: { tone: NoticeTone; text: string };
  onRunIdChange: (runId: string) => void;
  onLoad: (runId?: string) => void;
  onClearRecent: () => void;
};

export function RunPicker({
  runId,
  recentRunIds,
  busy,
  notice,
  onRunIdChange,
  onLoad,
  onClearRecent,
}: RunPickerProps) {
  const statusStyle = notice.tone === "error"
    ? [styles.status, styles.statusError]
    : notice.tone === "success"
    ? [styles.status, styles.statusSuccess]
    : styles.status;

  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>Open an RFQ</Text>
      <Text style={styles.sectionCopy}>
        Load a durable run ID from QuoteHawk. Only the five most recent opaque run IDs
        are stored locally; no quotes, buyer IDs, or credentials are retained on-device.
      </Text>
      <Text style={styles.label}>RFQ run ID</Text>
      <TextInput
        value={runId}
        onChangeText={onRunIdChange}
        placeholder="rfq-…"
        placeholderTextColor={colors.muted}
        autoCapitalize="none"
        autoCorrect={false}
        editable={!busy}
        accessibilityLabel="RFQ run ID"
        style={styles.input}
      />
      <Pressable
        onPress={() => onLoad()}
        disabled={busy}
        accessibilityRole="button"
        accessibilityLabel="Load RFQ run"
        style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed, busy && styles.disabled]}
      >
        {busy ? <ActivityIndicator color={colors.accentInk} /> : <Text style={styles.primaryButtonText}>Load RFQ review</Text>}
      </Pressable>
      <Text accessibilityLiveRegion="polite" style={statusStyle}>{notice.text}</Text>
      {recentRunIds.length > 0 && (
        <View style={styles.cardInset}>
          <View style={styles.row}>
            <Text style={styles.label}>Recent on this device</Text>
            <Pressable onPress={onClearRecent} disabled={busy} accessibilityRole="button">
              <Text style={styles.recentPillText}>Clear</Text>
            </Pressable>
          </View>
          <View style={styles.pillRow}>
            {recentRunIds.map((recentRunId) => (
              <Pressable
                key={recentRunId}
                onPress={() => onLoad(recentRunId)}
                disabled={busy}
                accessibilityRole="button"
                accessibilityLabel={`Load recent RFQ ${recentRunId}`}
                style={({ pressed }) => [styles.recentPill, pressed && styles.pressed, busy && styles.disabled]}
              >
                <Text numberOfLines={1} style={styles.recentPillText}>{recentRunId}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      )}
    </View>
  );
}
