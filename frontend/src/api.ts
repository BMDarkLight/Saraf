export type AgentEvent =
  | { type: "session"; session_id: string }
  | { type: "text"; delta: string }
  | { type: "reasoning"; delta: string }
  | { type: "tool_call"; id: string; name: string; args: any }
  | { type: "tool_result"; id: string; name: string; ok: boolean; result: any }
  | { type: "confirm"; action: Action }
  | { type: "action_update"; action: Action }
  | { type: "error"; message: string }
  | { type: "done" };

export interface Action {
  id: string;
  tool: string;
  args: Record<string, any>;
  summary: string;
  reason?: string | null;
  risk: Record<string, any>;
  status: "pending" | "executing" | "executed" | "rejected" | "failed" | "expired";
  result?: any;
}

export async function getJSON<T = any>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
}

/** POST and consume a Server-Sent Events stream. */
export async function streamSSE(url: string, body: unknown, onEvent: (e: AgentEvent) => void, signal?: AbortSignal) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
