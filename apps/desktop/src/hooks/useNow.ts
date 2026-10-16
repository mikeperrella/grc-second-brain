// Thin hook: the current time in ms, refreshed once a second while `active`. It feeds the local
// counters ("time since the last answer", "running for"); it never touches the sidecar.

import { useEffect, useState } from "react";

export function useNow(active: boolean): number {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!active) {
      return undefined;
    }
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  return now;
}
