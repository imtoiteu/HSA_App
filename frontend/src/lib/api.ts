// Thin fetch wrapper: JSON, cookies, CSRF double-submit header, normalised errors.

export class ApiError extends Error {
  status: number;
  code: string;
  data: Record<string, unknown>;
  constructor(status: number, code: string, message: string, data: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)hsa_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

type Opts = { method?: string; body?: unknown; keepalive?: boolean; signal?: AbortSignal; raw?: boolean };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method || (opts.body !== undefined ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body, credentials: "same-origin", keepalive: opts.keepalive, signal: opts.signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "Không kết nối được máy chủ. Kiểm tra kết nối mạng.");
  }
  if (opts.raw) {
    if (!res.ok) throw new ApiError(res.status, "http", `Lỗi ${res.status}`);
    return (await res.text()) as unknown as T;
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const d = data?.detail;
    if (d && typeof d === "object") throw new ApiError(res.status, d.code || "error", d.message || "Có lỗi xảy ra.", d);
    throw new ApiError(res.status, "http", typeof d === "string" ? d : `Có lỗi xảy ra (${res.status}).`);
  }
  return data as T;
}

export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, body: unknown = {}) => api<T>(p, { method: "POST", body });
export const put = <T = any>(p: string, body: unknown) => api<T>(p, { method: "PUT", body });
export const patch = <T = any>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });

export function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}
