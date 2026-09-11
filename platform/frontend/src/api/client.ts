/**
 * The single route to the CASS API.
 *
 * Build plan section 4 states the boundary plainly: the React application
 * calls only the CASS API and never an engine API directly. Keeping one client
 * makes that checkable -- a `fetch` anywhere else in the application is a bug.
 */

import type { Paginated } from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  /** True where the failure is a governance refusal rather than a fault. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match?.[2] ? decodeURIComponent(match[2]) : null;
}

/**
 * Extract the message the API intended a person to read.
 *
 * The backend returns refusals with a `detail` written in business language,
 * and validation errors keyed by field. Surfacing the raw JSON instead would
 * throw away the work the API did to explain itself.
 */
function messageFrom(status: number, body: unknown): string {
  if (typeof body === "string" && body.trim()) return body;
  if (body && typeof body === "object") {
    const record = body as Record<string, unknown>;
    if (typeof record.detail === "string") return record.detail;
    const fieldErrors = Object.entries(record)
      .filter(([, value]) => Array.isArray(value))
      .map(([field, value]) => `${field}: ${(value as unknown[]).join(" ")}`);
    if (fieldErrors.length) return fieldErrors.join("; ");
  }
  return `The request failed with status ${status}.`;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  /** Set for multipart uploads, where the browser writes the content type. */
  formData?: FormData;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, formData } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  const csrf = readCookie("csrftoken");
  if (csrf && method !== "GET" && method !== "HEAD") {
    headers["X-CSRFToken"] = csrf;
  }

  let payload: BodyInit | undefined;
  if (formData) {
    payload = formData;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: payload,
    credentials: "same-origin",
    signal,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const parsed: unknown = text ? safeParse(text) : null;

  if (!response.ok) {
    throw new ApiError(response.status, messageFrom(response.status, parsed), parsed);
  }
  return parsed as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | boolean | undefined>) =>
    request<T>(`${path}${params ? query(params) : ""}`),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, formData: FormData) =>
    request<T>(path, { method: "POST", formData }),
};

/** Unwrap a paginated envelope for callers that only want the page. */
export function rows<T>(page: Paginated<T> | T[]): T[] {
  return Array.isArray(page) ? page : page.results;
}
