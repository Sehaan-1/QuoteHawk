import { Text, View } from "react-native";

import { formatAuditTime, humanizeEventType } from "../format";
import { styles } from "../theme";
import type { AuditEvent } from "../types";

type AuditTimelineProps = {
  events: AuditEvent[];
};

export function AuditTimeline({ events }: AuditTimelineProps) {
  const ordered = [...events].sort((left, right) => right.sequence - left.sequence);

  return (
    <View style={styles.card}>
      <Text style={styles.sectionTitle}>Decision record</Text>
      <Text style={styles.sectionCopy}>
        This is the persisted RFQ audit trail. It intentionally shows policy facts rather than hidden model reasoning.
      </Text>
      {ordered.length === 0 ? <Text style={styles.empty}>No persisted audit events are available for this run.</Text> : (
        <View style={styles.timeline}>
          {ordered.map((event, index) => (
            <View key={event.sequence} style={styles.timelineItem}>
              <View style={styles.timelineRail}>
                <View style={styles.timelineDot} />
                {index < ordered.length - 1 && <View style={styles.timelineLine} />}
              </View>
              <View style={styles.timelineBody}>
                <Text style={styles.timelineTitle}>{humanizeEventType(event.event_type)}</Text>
                <Text style={styles.timelineReason}>{event.reason}</Text>
                <Text style={styles.timelineTime}>{formatAuditTime(event.occurred_at)}</Text>
              </View>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}
