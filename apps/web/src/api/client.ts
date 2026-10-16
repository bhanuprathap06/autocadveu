import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

/** Error raised for any non-2xx API response, mirroring the server error schema. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
    public requestId?: string | null,
  ) {
    super(message);
  }
}

export function csrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(/(?:^|;\s*)af_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

const csrf: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const t = csrfToken();
      if (t) request.headers.set("x-csrf-token", t);
    }
    return request;
  },
};

export const client = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
client.use(csrf);

async function toError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } = {};
  try {
    body = await res.json();
  } catch {
    /* non-JSON error (proxy, network) */
  }
  const e = body.error ?? {};
  const msg =
    e.message ??
    (res.status === 0 || res.status >= 502
      ? "The server is unreachable. Check your connection and try again."
      : `Request failed (${res.status})`);
  return new ApiError(res.status, e.code ?? "http_error", msg, e.details, e.request_id ?? res.headers.get("x-request-id"));
}

/** Untyped-path escape hatch for endpoints that return files or need raw responses. */
export async function raw(method: string, path: string, body?: unknown, init?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  if (body !== undefined && !(body instanceof FormData)) headers["content-type"] = "application/json";
  if (!["GET", "HEAD"].includes(method)) {
    const t = csrfToken();
    if (t) headers["x-csrf-token"] = t;
  }
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      ...init,
      headers,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network_error", "Network error: the request did not reach the server.");
  }
  if (!res.ok) throw await toError(res);
  return res;
}

/** JSON request helper. Paths are checked against the generated OpenAPI types where possible via `api.*` wrappers. */
export async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await raw(method, path, body);
  return (await res.json()) as T;
}

export const get = <T,>(path: string) => json<T>("GET", path);
export const post = <T,>(path: string, body?: unknown) => json<T>("POST", path, body ?? {});
export const put = <T,>(path: string, body?: unknown) => json<T>("PUT", path, body ?? {});
export const patch = <T,>(path: string, body?: unknown) => json<T>("PATCH", path, body ?? {});
export const del = <T,>(path: string) => json<T>("DELETE", path);

export async function download(method: string, path: string, body?: unknown, fallbackName = "download") {
  const res = await raw(method, path, body);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  return { name, sha256: res.headers.get("x-content-sha256"), verification: res.headers.get("x-export-verification") };
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

/**
 * Build a concrete URL from an API path template. The template is type-checked against the generated OpenAPI
 * paths, so a renamed or removed backend route is a compile error in the web app.
 */
export function apiPath<P extends keyof paths & string>(template: P, params: Record<string, string | number> = {},
  query?: Record<string, string | number | boolean | undefined | null | string[]>): string {
  let out: string = template.replace(/\{(\w+)\}/g, (_, k: string) => {
    if (params[k] === undefined) throw new Error(`Missing path parameter ${k} for ${template}`);
    return encodeURIComponent(String(params[k]));
  });
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) out += `?${s}`;
  }
  return out;
}
