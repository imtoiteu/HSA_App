import { useCallback, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { STATE_LABELS } from "../../lib/format";

export function useUrlState(defaults: Record<string, string> = {}) {
  const [sp, setSp] = useSearchParams();
  const get = (k: string) => sp.get(k) ?? defaults[k] ?? "";
  const set = useCallback((patch: Record<string, string | number | null | undefined>, resetPage = true) => {
    setSp((prev) => {
      const next = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === "") next.delete(k);
        else next.set(k, String(v));
      }
      if (resetPage && !("page" in patch)) next.delete("page");
      return next;
    }, { replace: true });
  }, [setSp]);
  return { get, set, params: sp };
}

const STATE_TONE: Record<string, string> = {
  READY_TO_SERVE: "ok", NEEDS_REVIEW: "warn", NEEDS_FORMULA_REVIEW: "warn", NEEDS_VISUAL_REVIEW: "warn",
  NEEDS_ANSWER_LINKING: "accent", REJECTED: "bad",
};

export function StateBadge({ state }: { state: string }) {
  return <span className={"badge " + (STATE_TONE[state] || "")} title={state}>{STATE_LABELS[state] || state}</span>;
}

export function ServedBadge({ served }: { served: boolean }) {
  return served ? <span className="badge ok">Đang phục vụ</span> : <span className="badge">Không phục vụ</span>;
}

export function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="row between mb">
      <h1 style={{ margin: 0 }}>{title}</h1>
      {children && <div className="row">{children}</div>}
    </div>
  );
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const errs = (e.data?.errors as { loc: string; msg: string }[] | undefined) || [];
    return errs.length ? `${e.message} ${errs.map((x) => `${x.loc}: ${x.msg}`).join("; ")}` : e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export function copyText(t: string) {
  try {
    void navigator.clipboard.writeText(t);
  } catch {
    /* ignore */
  }
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function numOrNull(s: string): number | null {
  if (s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}
