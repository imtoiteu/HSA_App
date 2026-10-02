import { useState } from "react";
import { get, post, put, qs } from "../../lib/api";
import { dateTime, vnd } from "../../lib/format";
import { Confirm, Empty, ErrorBox, Modal, Pager, Spinner, useAsync, useToast } from "../../components/ui";
import { errMsg, numOrNull, PageHead, useUrlState } from "./common";

// ------------------------------------------------------------------------------------------ products
interface Product {
  id: number; code: string; name: string; description: string | null; price_vnd: number; attempts: number | null;
  duration_days: number | null; blueprint_ids: number[]; is_active: boolean; sort_order: number;
}
interface BPLite { id: number; name: string; price_vnd: number }
type PForm = { code: string; name: string; description: string; price_vnd: string; attempts: string; duration_days: string; blueprint_ids: number[]; is_active: boolean; sort_order: string };
const toForm = (p?: Product): PForm => ({
  code: p?.code || "", name: p?.name || "", description: p?.description || "", price_vnd: String(p?.price_vnd ?? ""),
  attempts: p?.attempts != null ? String(p.attempts) : "", duration_days: p?.duration_days != null ? String(p.duration_days) : "",
  blueprint_ids: p?.blueprint_ids || [], is_active: p?.is_active ?? true, sort_order: String(p?.sort_order ?? 100),
});

export function Products() {
  const list = useAsync(() => get<{ items: Product[] }>("/api/admin/products"), []);
  const bps = useAsync(() => get<{ items: BPLite[] }>("/api/admin/blueprints"), []);
  const [edit, setEdit] = useState<{ id: number | null; f: PForm } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const bpName = (id: number) => bps.data?.items.find((b) => b.id === id)?.name || `#${id}`;

  const save = async () => {
    if (!edit) return;
    const f = edit.f;
    setErr("");
    const body = {
      code: f.code, name: f.name, description: f.description || null, price_vnd: Number(f.price_vnd),
      attempts: numOrNull(f.attempts), duration_days: numOrNull(f.duration_days), blueprint_ids: f.blueprint_ids,
      is_active: f.is_active, sort_order: Number(f.sort_order) || 100,
    };
    if (!(body.price_vnd > 0)) { setErr("Giá phải lớn hơn 0."); return; }
    setBusy(true);
    try {
      if (edit.id) await put(`/api/admin/products/${edit.id}`, body); else await post("/api/admin/products", body);
      toast("Đã lưu gói.", "ok"); setEdit(null); list.reload();
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  const set = (patch: Partial<PForm>) => edit && setEdit({ ...edit, f: { ...edit.f, ...patch } });

  return (
    <div>
      <PageHead title="Gói bán"><button className="btn" onClick={() => { setErr(""); setEdit({ id: null, f: toForm() }); }}>+ Thêm gói</button></PageHead>
      <div className="alert info mb">Giá từng lượt thi đặt trực tiếp trên mỗi đề (Cấu trúc đề). Gói bán dùng cho combo nhiều lượt hoặc thẻ thời hạn.</div>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Chưa có gói nào." /> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Gói</th><th>Giá</th><th>Lượt</th><th>Thời hạn</th><th>Áp dụng</th><th>Trạng thái</th><th /></tr></thead>
          <tbody>{list.data.items.map((p) => (
            <tr key={p.id}>
              <td><strong>{p.name}</strong><div className="small muted mono">{p.code}</div></td>
              <td>{vnd(p.price_vnd)}</td>
              <td>{p.attempts ?? "Không giới hạn"}</td>
              <td>{p.duration_days ? `${p.duration_days} ngày` : "Vĩnh viễn"}</td>
              <td className="small">{p.blueprint_ids.length ? p.blueprint_ids.map(bpName).join(", ") : "Mọi đề có phí"}</td>
              <td>{p.is_active ? <span className="badge ok">Đang bán</span> : <span className="badge">Tạm ẩn</span>}</td>
              <td><button className="btn secondary sm" onClick={() => { setErr(""); setEdit({ id: p.id, f: toForm(p) }); }}>Sửa</button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {edit && (
        <Modal title={edit.id ? "Sửa gói" : "Thêm gói"} onClose={() => setEdit(null)} actions={<>
          <button className="btn secondary" onClick={() => setEdit(null)}>Huỷ</button>
          <button className="btn" disabled={busy} onClick={save}>Lưu</button>
        </>}>
          <div className="stack">
            <div className="field"><label htmlFor="p-code">Mã</label><input id="p-code" className="input" value={edit.f.code} onChange={(e) => set({ code: e.target.value })} /></div>
            <div className="field"><label htmlFor="p-name">Tên</label><input id="p-name" className="input" value={edit.f.name} onChange={(e) => set({ name: e.target.value })} /></div>
            <div className="field"><label htmlFor="p-desc">Mô tả</label><textarea id="p-desc" className="input" value={edit.f.description} onChange={(e) => set({ description: e.target.value })} /></div>
            <div className="row">
              <div className="field grow"><label htmlFor="p-price">Giá (VNĐ)</label><input id="p-price" className="input" type="number" min={1000} step={1000} value={edit.f.price_vnd} onChange={(e) => set({ price_vnd: e.target.value })} /></div>
              <div className="field grow"><label htmlFor="p-att">Số lượt (trống = không giới hạn)</label><input id="p-att" className="input" type="number" min={1} value={edit.f.attempts} onChange={(e) => set({ attempts: e.target.value })} /></div>
              <div className="field grow"><label htmlFor="p-days">Số ngày (trống = vĩnh viễn)</label><input id="p-days" className="input" type="number" min={1} value={edit.f.duration_days} onChange={(e) => set({ duration_days: e.target.value })} /></div>
            </div>
            <div className="field"><span className="label">Áp dụng cho đề (không chọn = mọi đề có phí)</span>
              <div className="chip-group">
                {bps.data?.items.map((b) => {
                  const on = edit.f.blueprint_ids.includes(b.id);
                  return <button key={b.id} type="button" className={"chip" + (on ? " on" : "")} aria-pressed={on}
                                 onClick={() => set({ blueprint_ids: on ? edit.f.blueprint_ids.filter((x) => x !== b.id) : [...edit.f.blueprint_ids, b.id] })}>{b.name}</button>;
                })}
              </div></div>
            <div className="row">
              <label className="check"><input type="checkbox" checked={edit.f.is_active} onChange={(e) => set({ is_active: e.target.checked })} /> Đang bán</label>
              <div className="field"><label htmlFor="p-sort">Thứ tự</label><input id="p-sort" className="input" type="number" value={edit.f.sort_order} onChange={(e) => set({ sort_order: e.target.value })} /></div>
            </div>
            {err && <div className="error-text">{err}</div>}
          </div>
        </Modal>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ transactions
interface Txn {
  id: number; provider: string; provider_txn_id: string; amount_vnd: number; content: string | null; occurred_at: string | null;
  received_at: string; order_id: number | null; status: string; note: string | null;
}
const TX_TONE: Record<string, string> = { matched: "ok", unmatched: "bad", underpaid: "warn", late: "warn", ignored: "" };

export function Transactions() {
  const u = useUrlState();
  const page = Number(u.get("page") || 1);
  const list = useAsync(() => get<{ total: number; items: Txn[] }>("/api/admin/transactions" + qs({ status: u.get("status"), page, size: 50 })),
    [u.get("status"), page]);
  const [assign, setAssign] = useState<{ t: Txn; code: string; note: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const doAssign = async () => {
    if (!assign) return;
    if (!assign.code.trim()) { toast("Nhập mã đơn hàng.", "error"); return; }
    setBusy(true);
    try {
      await post(`/api/admin/transactions/${assign.t.id}/assign`, { order_code: assign.code.trim(), note: assign.note || null });
      toast("Đã gán giao dịch vào đơn và cấp quyền.", "ok"); setAssign(null); list.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  return (
    <div>
      <PageHead title="Giao dịch ngân hàng" />
      <div className="tabs">
        {[["", "Tất cả"], ["unmatched", "Chưa khớp"], ["underpaid", "Thiếu tiền"], ["late", "Cần xem lại"], ["matched", "Đã khớp"], ["ignored", "Bỏ qua"]].map(([k, l]) => (
          <button key={k} className={u.get("status") === k ? "active" : ""} onClick={() => u.set({ status: k })}>{l}</button>))}
      </div>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Không có giao dịch." /> : (
        <>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>#</th><th>Kênh</th><th>Mã GD</th><th>Số tiền</th><th>Nội dung</th><th>Thời gian</th><th>Trạng thái</th><th /></tr></thead>
            <tbody>{list.data.items.map((t) => (
              <tr key={t.id}>
                <td>{t.id}</td><td>{t.provider}</td><td className="mono small">{t.provider_txn_id}</td><td>{vnd(t.amount_vnd)}</td>
                <td className="small" style={{ maxWidth: 300 }}>{t.content}</td>
                <td className="small nowrap">{dateTime(t.occurred_at || t.received_at)}</td>
                <td><span className={"badge " + (TX_TONE[t.status] || "")}>{t.status}</span>{t.note && <div className="small muted">{t.note}</div>}</td>
                <td>{t.status !== "matched" && t.status !== "ignored" && (
                  <button className="btn secondary sm" onClick={() => setAssign({ t, code: "", note: "" })}>Gán vào đơn</button>)}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pager page={page} size={50} total={list.data.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
      {assign && (
        <Confirm title={`Gán giao dịch #${assign.t.id}`} confirmText="Gán và cấp quyền" busy={busy} onConfirm={doAssign} onClose={() => setAssign(null)}>
          <div className="stack">
            <div className="small">Số tiền {vnd(assign.t.amount_vnd)} · nội dung: <em>{assign.t.content}</em></div>
            <div className="field"><label htmlFor="as-code">Mã đơn hàng</label>
              <input id="as-code" className="input mono" value={assign.code} onChange={(e) => setAssign({ ...assign, code: e.target.value.toUpperCase() })} placeholder="HSAXXXXXXXX" /></div>
            <div className="field"><label htmlFor="as-note">Ghi chú</label>
              <input id="as-note" className="input" value={assign.note} onChange={(e) => setAssign({ ...assign, note: e.target.value })} placeholder="vd: khách ghi sai nội dung" /></div>
          </div>
        </Confirm>
      )}
    </div>
  );
}
