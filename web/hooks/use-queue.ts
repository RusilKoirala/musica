"use client";

import { useEffect, useRef, useState } from "react";
import { eventsUrl, type LogEntry, type QueueState } from "@/lib/api";

// One EventSource for the whole page. The backend sends every frame as a `snapshot`
// event whose JSON says what it really is: a log line or a full queue state.
export function useQueue(enabled: boolean) {
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | null = null;
    let closed = false;

    const open = () => {
      source = new EventSource(eventsUrl(), { withCredentials: true });
      source.onopen = () => setConnected(true);
      source.addEventListener("snapshot", (event) => {
        try {
          const frame = JSON.parse((event as MessageEvent).data);
          if (frame.event === "log") {
            setLogs((current) => [...current.slice(-299), frame.payload]);
          } else if (frame.payload && Array.isArray(frame.payload.jobs)) {
            setQueue(frame.payload);
          }
        } catch {
          // A malformed frame is not worth breaking the page over.
        }
      });
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry.current = setTimeout(open, 2000);
      };
    };

    open();
    return () => {
      closed = true;
      source?.close();
      if (retry.current) clearTimeout(retry.current);
    };
  }, [enabled]);

  return { queue, logs, connected };
}
