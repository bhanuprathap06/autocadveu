"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

export interface Presence {
  user_id: string;
  name: string;
  role: string;
  conn: string;
  document_id: string | null;
  camera: { position?: number[]; target?: number[] } | null;
  selection: string[];
  ts: number;
}

type Handler = (event: Record<string, any>) => void;

function wsUrl(projectId: string): string {
  const base =
    process.env.NEXT_PUBLIC_WS_URL ||
    (typeof window !== "undefined"
      ? window.location.port === "3000"
        ? `ws://${window.location.hostname}:8000` // local dev: API runs on :8000 (cookies are host-scoped)
        : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`
      : "");
  return `${base}/api/v1/ws?project_id=${encodeURIComponent(projectId)}`;
}

/**
 * Project realtime channel: presence plus durable-change notifications. Durable data is never taken from the
 * socket; events only invalidate React Query caches so state is refetched through the REST API.
 */
export function useProjectChannel(projectId: string | undefined, onEvent?: Handler) {
  const qc = useQueryClient();
  const [presence, setPresence] = useState<Record<string, Presence>>({});
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const handler = useRef<Handler | undefined>(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!projectId) return;
    let closed = false;
    let retry = 0;
    let ping: ReturnType<typeof setInterval> | undefined;
    const connect = () => {
      const ws = new WebSocket(wsUrl(projectId));
      socket.current = ws;
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
        ping = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 20_000);
      };
      ws.onmessage = (m) => {
        let ev: Record<string, any>;
        try {
          ev = JSON.parse(m.data);
        } catch {
          return;
        }
        if (ev.type === "hello") {
          const map: Record<string, Presence> = {};
          for (const p of ev.presence as Presence[]) if (p.conn !== ev.you.conn) map[p.conn] = p;
          setPresence(map);
        } else if (ev.type === "presence.join" || ev.type === "presence.update") {
          setPresence((s) => ({ ...s, [ev.presence.conn]: ev.presence }));
        } else if (ev.type === "presence.leave") {
          setPresence((s) => {
            const n = { ...s };
            delete n[ev.presence.conn];
            return n;
          });
        } else if (ev.type === "revision.created" || ev.type === "document.created" || ev.type === "document.deleted") {
          qc.invalidateQueries({ queryKey: ["project", projectId] });
          if (ev.document_id) qc.invalidateQueries({ queryKey: ["revisions", ev.document_id] });
        } else if (ev.type?.startsWith("comment.")) {
          qc.invalidateQueries({ queryKey: ["comments", projectId] });
        } else if (ev.type === "job.status") {
          qc.invalidateQueries({ queryKey: ["simulations", projectId] });
          qc.invalidateQueries({ queryKey: ["simulation"] });
        } else if (ev.type === "drawing.saved") {
          qc.invalidateQueries({ queryKey: ["drawing", ev.drawing_id] });
        }
        handler.current?.(ev);
      };
      ws.onclose = () => {
        setConnected(false);
        if (ping) clearInterval(ping);
        if (!closed) setTimeout(connect, Math.min(15_000, 1000 * 2 ** retry++));
      };
    };
    connect();
    return () => {
      closed = true;
      if (ping) clearInterval(ping);
      socket.current?.close();
    };
  }, [projectId, qc]);

  const send = (msg: Record<string, unknown>) => {
    const ws = socket.current;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
  };
  return { presence: Object.values(presence), connected, send };
}
