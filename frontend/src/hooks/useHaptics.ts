// Feature-detect and no-op silently — iOS Safari does not support the Vibration API
// at all, which is fine and expected. Nikki.md §2.
//
// Nothing upstream ever branches on support: on an iPhone every call here is a silent
// no-op, and the instruction still arrives with text, speech and a screen change. A
// missing buzz must never become a console error or a visible degradation.

import { useCallback } from "react";
import type { Urgency } from "../types/hive";

const PATTERNS: Record<Urgency, number[]> = {
  normal: [40],
  high: [60, 50, 60],
  critical: [100, 60, 100, 60, 100],
};

const SUPPORTED = typeof navigator !== "undefined" && typeof navigator.vibrate === "function";

/**
 * @param enabled false under scenario comms_profile 'silent' — CONTRACTS.md §comms
 *               disables speech *and* vibration together, since a buzzing pocket in a
 *               campus-emergency drill is exactly the tell the profile exists to avoid.
 */
export function useHaptics(enabled = true) {
  const buzz = useCallback(
    (urgency: Urgency = "normal") => {
      if (!enabled || !SUPPORTED) return;
      try {
        navigator.vibrate(PATTERNS[urgency] ?? PATTERNS.normal);
      } catch {
        // Some engines throw when the document isn't focused. Never surface it.
      }
    },
    [enabled],
  );

  // Cancel a running pattern — used when an instruction is withdrawn mid-buzz, so the
  // phone stops talking about an action that no longer exists.
  const stop = useCallback(() => {
    if (!SUPPORTED) return;
    try {
      navigator.vibrate(0);
    } catch {
      // as above
    }
  }, []);

  return { buzz, stop, supported: SUPPORTED };
}
