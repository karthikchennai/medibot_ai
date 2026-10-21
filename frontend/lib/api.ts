export type Role = "doctor" | "nurse" | "billing_executive" | "billing" | "technician" | "admin";
export type User = { username: string; name: string; role: Role };
export type Session = { user: User; collections: string[] };
export type LoginResponse = Session & { access_token: string; token_type: string };
export type Source = {
  id?: string;
  source_document: string;
  section_title: string;
  collection: string;
  page_number?: number | null;
  score?: number;
};
export type ChatResponse = {
  answer: string;
  sources: Source[];
  retrieval_type: "hybrid_rag" | "sql_rag";
  role: Role;
  blocked: boolean;
  sql?: string | null;
  metrics?: Record<string, unknown>;
};
export type Health = {
  status: string;
  provider?: string;
  fallback_provider?: string | null;
  checks: { llm_configured: boolean; bedrock_configured: boolean; groq_configured: boolean; index_ready: boolean; [key: string]: unknown };
};

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
export const SESSION_KEY = "medibot.session";

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("We couldn’t connect to the MediBot server. Check that the backend is running and try again.", 0);
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new ApiError(message || `The server couldn’t complete this request (${response.status}). Please try again.`, response.status, detail?.code);
  }
  return data as T;
}
