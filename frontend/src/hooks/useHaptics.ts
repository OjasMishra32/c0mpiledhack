// Feature-detect and no-op silently — iOS Safari does not support the Vibration API
// at all, which is fine and expected. Nikki.md §2.

import { useCallback } from "react";
import type { Urgency } from "../types/hive";

const PATTERNS: Record<Urgency, number[]> = {
  normal: [40],
  high: [60, 50, 60],
  critical: [100, 60, 100, 60, 100],
};

export function useHaptics() {
  const buzz = useCallback((urgency: Urgency) => {
    navigator.vibrate?.(PATTERNS[urgency]);
  }, []);
  return { buzz };
}
