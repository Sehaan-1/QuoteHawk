import { Text, View } from "react-native";

import { styles } from "../theme";

export function AppHeader() {
  return (
    <>
      <View style={styles.header}>
        <View style={styles.mark}><Text style={styles.markText}>Q</Text></View>
        <View>
          <Text style={styles.brand}>QuoteHawk</Text>
          <Text style={styles.subBrand}>RFQ buyer review</Text>
        </View>
      </View>
      <View style={styles.boundary} accessibilityRole="summary">
        <Text style={styles.boundaryKicker}>DRAFT-ONLY CONTROL SURFACE</Text>
        <Text style={styles.boundaryText}>
          Review validated bid facts and record a buyer decision. This companion cannot
          email a supplier, issue an order, or write to an ERP.
        </Text>
      </View>
    </>
  );
}
