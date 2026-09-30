import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export function Spinner({ label = "Đang tải…" }: { label?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <div className="spinner" />
      <span className="sr-only">{label}</span>
    </div>
  );
}

export function Empty({ icon = "📭", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="big" aria-hidden>{icon}</div>
      <div style={{ fontWeight: 600, color: "var(--ink-2)" }}>{title}</div>
      {children && <div className="mt">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="alert error" role="alert">{msg}</div>;
}

export function Modal({ title, onClose, children, actions, wide }: {
  title?: string; onClose: () => void; children: ReactNode; actions?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={"modal" + (wide ? " wide" : "")} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        {title && <h2>{title}</h2>}
        {children}
        {actions && <div className="modal-actions">{actions}</div>}
      </div>
    </div>
  );
}

type Toast = { id: number; text: string; kind: "info" | "ok" | "error" };
const ToastCtx = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-wrap" aria-live="polite">
        {items.map((t) => <div key={t.id} className={"toast " + t.kind}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

export function Confirm({ title, children, confirmText = "Xác nhận", danger, onConfirm, onClose, busy }: {
  title: string; children: ReactNode; confirmText?: string; danger?: boolean; onConfirm: () => void; onClose: () => void; busy?: boolean;
}) {
  return (
    <Modal title={title} onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose} disabled={busy}>Huỷ</button>
      <button className={"btn " + (danger ? "danger" : "")} onClick={onConfirm} disabled={busy}>{busy ? "Đang xử lý…" : confirmText}</button>
    </>}>
      {children}
    </Modal>
  );
}

export function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <span className="muted small">Trang {page}/{pages} · {total.toLocaleString("vi-VN")} mục</span>
      <button className="btn secondary sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Trước</button>
      <button className="btn secondary sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Sau ›</button>
    </div>
  );
}

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const p: Record<string, ReactNode> = {
    flag: <path d="M5 21V4m0 0h11l-2 4 2 4H5" />,
    bookmark: <path d="M6 3h12v18l-6-4-6 4z" />,
    report: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6m0 4h.01" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    left: <path d="M15 5l-7 7 7 7" />,
    right: <path d="M9 5l7 7-7 7" />,
    close: <path d="M6 6l12 12M18 6L6 18" />,
    check: <path d="M5 12l5 5 9-10" />,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {p[name]}
    </svg>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn().then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error, loading: false }));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
