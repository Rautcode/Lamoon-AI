import { api } from "@/lib/api";

/* Lumo, client side.

   This used to be a keyword router that made several API calls per question.
   The routing, the tools, and (when GEMINI_API_KEY is configured) the model
   now live server-side in app/modules/assistant/ — one round trip, one place
   the grounding rules are enforced, and the browser never needs to know which
   path answered.

   `model_used` tells the UI whether a real model wrote the prose or the
   deterministic fallback did, so the interface can be honest about it rather
   than implying intelligence that isn't switched on. */

export type LumoItem = { title: string; meta?: string | null; href?: string | null };
export type LumoAnswer = {
  text: string;
  items?: LumoItem[];
  /** Lumo genuinely couldn't parse the request — the UI offers capabilities. */
  unmatched?: boolean;
  /** False when the keyword fallback answered (no model configured). */
  modelUsed?: boolean;
};

export async function askLumo(raw: string): Promise<LumoAnswer> {
  const question = raw.trim();
  if (!question) return { text: "Ask me anything about your people, hiring, or time off." };

  const res = await api.assistant.ask(question);
  return {
    text: res.text,
    items: res.items,
    unmatched: res.unmatched,
    modelUsed: res.model_used,
  };
}
