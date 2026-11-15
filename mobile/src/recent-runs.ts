import AsyncStorage from "@react-native-async-storage/async-storage";

const RECENT_RUNS_KEY = "quotehawk.recent-run-ids.v1";
const MAX_RECENT_RUNS = 5;

function normalizeRunId(value: string) {
  return value.trim().slice(0, 100);
}

export async function loadRecentRunIds(): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(RECENT_RUNS_KEY);
    const decoded: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(decoded)) return [];
    return decoded
      .filter((runId): runId is string => typeof runId === "string" && Boolean(normalizeRunId(runId)))
      .map(normalizeRunId)
      .slice(0, MAX_RECENT_RUNS);
  } catch {
    // Recent IDs are a convenience only; failure cannot block buyer review.
    return [];
  }
}

export async function rememberRunId(runId: string): Promise<string[]> {
  const normalized = normalizeRunId(runId);
  if (!normalized) return loadRecentRunIds();
  const recent = await loadRecentRunIds();
  const next = [normalized, ...recent.filter((candidate) => candidate !== normalized)].slice(
    0,
    MAX_RECENT_RUNS,
  );
  try {
    await AsyncStorage.setItem(RECENT_RUNS_KEY, JSON.stringify(next));
  } catch {
    // Do not turn a local convenience write into an approval-path failure.
  }
  return next;
}

export async function clearRecentRunIds(): Promise<void> {
  try {
    await AsyncStorage.removeItem(RECENT_RUNS_KEY);
  } catch {
    // The caller can still clear its in-memory list.
  }
}
