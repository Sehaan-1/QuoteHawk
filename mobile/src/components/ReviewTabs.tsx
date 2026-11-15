import { Pressable, Text, View } from "react-native";

import { styles } from "../theme";
import type { RfqView } from "../types";

type ReviewTabsProps = {
  value: RfqView;
  onChange: (view: RfqView) => void;
};

export function ReviewTabs({ value, onChange }: ReviewTabsProps) {
  return (
    <View style={styles.tabRow} accessibilityRole="tablist">
      <Tab label="Review" value="review" selected={value === "review"} onChange={onChange} />
      <Tab label="Activity" value="activity" selected={value === "activity"} onChange={onChange} />
    </View>
  );
}

function Tab({
  label,
  value,
  selected,
  onChange,
}: {
  label: string;
  value: RfqView;
  selected: boolean;
  onChange: (view: RfqView) => void;
}) {
  return (
    <Pressable
      onPress={() => onChange(value)}
      accessibilityRole="tab"
      accessibilityState={{ selected }}
      style={({ pressed }) => [styles.tab, selected && styles.tabActive, pressed && styles.pressed]}
    >
      <Text style={[styles.tabText, selected && styles.tabTextActive]}>{label}</Text>
    </Pressable>
  );
}
