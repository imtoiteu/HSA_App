import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { get, post, put, qs } from "../../lib/api";
import { dateOnly, dateTime, ORDER_STATUS, vnd } from "../../lib/format";
import { Confirm, Empty, ErrorBox, Pager, Spinner, useAsync, useToast } from "../../components/ui";
import { copyText, errMsg, numOrNull, PageHead, useUrlState } from "./common";

export interface AOrder {
  id: number; code: string; kind: "pro" | "exam" | "product"; plan_code: string | null; user_id: number;
  user_email: string | null; status: string; amount_vnd: number; list_price_vnd: number | null;
  paid_amount_vnd: number | null; name: string; transfer_content: string; provider: string | null; created_at: string;
  expires_at: string; paid_at: string | null; note: string | null; product_id: number | null; blueprint_id: number | null;
}
export const KIND_LABEL: Record<string, string> = { pro: "Gói Pro", exam: "Đề thi thử", product: "Gói lượt thi" };
const SRC_LABEL: Record<string, string> = { user: "Người mua", admin: "Quản trị", webhook: "Webhook ngân hàng", system: "Hệ thống" };

export function OrderBadge({ status }: { status: string }) {
  const [l, c] = ORDER_STATUS[status] || [status, ""];
  return <span className={"badge " + c}>{l}</span>;
}

// ------------------------------------------------------------------------------------------ orders list
export function Orders() {
  const u = useUrlState();
  const page = Number(u.get("page") || 1);
  const F = ["status", "kind", "q", "user", "reference", "amount_min", "amount_max", "date_from", "date_to"];
  const [f, setF] = useState<Record<string, string>>(() => Object.fromEntries(F.map((k) => [k, u.get(k)])));
  const list = useAsync(() => get<{ total: number; items: AOrder[] }>("/api/admin/orders" + qs({
    ...Object.fromEntries(F.map((k) => [k, u.get(k)])), page, size: 50,
  })), [...F.map((k) => u.get(k)), page]);
  const apply = (e: FormEvent) => { e.preventDefault(); u.set(f); };
  const field = (k: string, label: string, type = "text", w = 150) => (
    <div className="field" style={{ width: w }}><label htmlFor={`of-${k}`}>{label}</label>
      <input id={`of-${k}`} className="input" type={type} value={f[k] || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></div>
  );
  return (
    <div>
      <PageHead title="Thanh toán & đơn hàng">
        <Link to="/admin/giao-dich" className="btn secondary sm">Giao dịch ngân hàng</Link>
      </PageHead>
      <div className="tabs">
        {[["", "Tất cả"], ...Object.entries(ORDER_STATUS).map(([k, v]) => [k, v[0]])].map(([k, l]) => (
          <button key={k} className={u.get("status") === k ? "active" : ""} onClick={() => { setF({ ...f, status: k }); u.set({ status: k }); }}>{l}</button>))}
      </div>
      <form className="filters card flat" onSubmit={apply}>
        {field("q", "Mã đơn", "text", 150)}
        {field("user", "Người mua (email/tên)", "text", 200)}
        {field("reference", "Nội dung chuyển khoản", "text", 180)}
        <div className="field" style={{ width: 140 }}><label htmlFor="of-kind">Loại</label>
          <select id="of-kind" className="input" value={f.kind || ""} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="">Tất cả</option>{Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
        {field("amount_min", "Từ (VND)", "number", 110)}
        {field("amount_max", "Đến (VND)", "number", 110)}
        {field("date_from", "Từ ngày", "date", 150)}
        {field("date_to", "Đến ngày", "date", 150)}
        <button className="btn" type="submit">Lọc</button>
        <button className="btn ghost" type="button" onClick={() => { const empty = Object.fromEntries(F.map((k) => [k, ""])); setF(empty); u.set(empty); }}>Xoá lọc</button>
      </form>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Không có đơn hàng phù hợp." /> : (
        <>
          <div className="muted small mb">{list.data.total.toLocaleString("vi-VN")} đơn</div>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Mã đơn</th><th>Người mua</th><th>Loại / sản phẩm</th><th className="num">Số tiền</th><th>Nội dung CK</th><th>Trạng thái</th><th>Tạo lúc</th><th>Thanh toán</th></tr></thead>
            <tbody>{list.data.items.map((o) => (
              <tr key={o.id}>
                <td className="mono"><Link to={`/admin/don-hang/${o.code}`}>{o.code}</Link></td>
                <td><Link to={`/admin/nguoi-dung/${o.user_id}`}>{o.user_email || `#${o.user_id}`}</Link></td>
                <td className="small"><span className="badge">{KIND_LABEL[o.kind] || o.kind}</span> {o.name}</td>
                <td className="num">{vnd(o.amount_vnd)}{o.paid_amount_vnd != null && o.paid_amount_vnd !== o.amount_vnd && <div className="small muted">nhận {vnd(o.paid_amount_vnd)}</div>}</td>
                <td className="mono small">{o.transfer_content}</td>
                <td><OrderBadge status={o.status} />{o.provider && <div className="small muted">{o.provider}</div>}</td>
                <td className="small nowrap">{dateTime(o.created_at)}</td>
                <td className="small nowrap">{dateTime(o.paid_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pager page={page} size={50} total={list.data.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ order detail
interface OrderDetailResp {
  order: AOrder & { product_snapshot: Record<string, any>; bank_snapshot: Record<string, string> | null; confirmed_by_email: string | null };
  user: { id: number; email: string; display_name: string; plan: string } | null;
  events: { at: string; from: string | null; to: string | null; source: string; actor: string | null; note: string | null; data: Record<string, any> }[];
  transactions: { id: number; provider: string; provider_txn_id: string; amount_vnd: number; content: string | null; status: string; note: string | null; received_at: string }[];
  subscription: { starts_at: string; expires_at: string; state: string } | null;
  entitlement: { id: number; note: string; attempts_total: number | null; attempts_used: number; valid_until: string | null; status: string } | null;
}
type Act = "confirm" | "cancel" | "fail" | "refund" | "note";
const ACT: Record<Act, [string, string, boolean]> = {
  confirm: ["Xác nhận đã nhận tiền", "Xác nhận thủ công", false],
  cancel: ["Huỷ đơn", "Huỷ đơn", true],
  fail: ["Đánh dấu thất bại", "Đánh dấu thanh toán thất bại", true],
  refund: ["Hoàn tiền & thu hồi quyền", "Hoàn tiền", true],
  note: ["Lưu ghi chú", "Thêm ghi chú đối soát", false],
};

export function OrderDetail() {
  const { code = "" } = useParams();
  const r = useAsync(() => get<OrderDetailResp>(`/api/admin/orders/${code}`), [code]);
  const [act, setAct] = useState<Act | null>(null);
  const [note, setNote] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  if (r.loading) return <Spinner />;
  if (r.error || !r.data) return <ErrorBox error={r.error} />;
  const { order: o, user, events, transactions, subscription, entitlement } = r.data;
  const open = (a: Act) => { setAct(a); setNote(""); setAmount(String(o.amount_vnd)); };
  const run = async () => {
    if (!act) return;
    setBusy(true);
    try {
      const path = { confirm: "confirm", cancel: "cancel", fail: "fail", refund: "refund", note: "note" }[act];
      const body = act === "confirm" ? { amount_vnd: numOrNull(amount), note: note || null } : { note };
      const res = await post<{ confirmed?: boolean }>(`/api/admin/orders/${o.code}/${path}`, body);
      if (act === "confirm" && res.confirmed === false) toast("Đơn đã được xác nhận trước đó – không cấp quyền lần hai.", "info");
      else toast("Đã lưu.", "ok");
      setAct(null); r.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const canConfirm = ["pending", "expired", "cancelled", "failed"].includes(o.status);
  const needsNote = act !== "confirm" || o.status !== "pending";
  const b = o.bank_snapshot;
  return (
    <div>
      <PageHead title={`Đơn ${o.code}`}>
        <Link to="/admin/don-hang" className="btn ghost sm">← Danh sách đơn</Link>
      </PageHead>
      <div className="row mb" style={{ flexWrap: "wrap", gap: 8 }}>
        <OrderBadge status={o.status} />
        {canConfirm && <button className="btn ok sm" onClick={() => open("confirm")}>Xác nhận đã thanh toán</button>}
        {["pending", "expired"].includes(o.status) && <button className="btn secondary sm" onClick={() => open("cancel")}>Huỷ đơn</button>}
        {["pending", "expired"].includes(o.status) && <button className="btn secondary sm" onClick={() => open("fail")}>Đánh dấu thất bại</button>}
        {o.status === "paid" && <button className="btn danger sm" onClick={() => open("refund")}>Hoàn tiền</button>}
        <button className="btn ghost sm" onClick={() => open("note")}>Thêm ghi chú</button>
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h3>Đơn hàng</h3>
          <dl className="kv">
            <dt>Sản phẩm</dt><dd><span className="badge">{KIND_LABEL[o.kind]}</span> {o.name}</dd>
            <dt>Số tiền phải trả</dt><dd><strong>{vnd(o.amount_vnd)}</strong>{o.list_price_vnd && o.list_price_vnd !== o.amount_vnd ? <span className="muted small"> (giá niêm yết {vnd(o.list_price_vnd)})</span> : null}</dd>
            <dt>Giá cấu hình lúc tạo</dt><dd>{vnd(o.list_price_vnd ?? o.amount_vnd)}{o.product_snapshot.duration_days ? ` · ${o.product_snapshot.duration_days} ngày` : ""}{o.product_snapshot.attempts ? ` · ${o.product_snapshot.attempts} lượt` : ""}</dd>
            <dt>Nội dung CK</dt><dd className="row" style={{ gap: 4 }}><span className="mono">{o.transfer_content}</span><button className="btn ghost sm" onClick={() => copyText(o.transfer_content)}>Sao chép</button></dd>
            <dt>Tạo lúc</dt><dd>{dateTime(o.created_at)}</dd>
            <dt>Hết hạn lúc</dt><dd>{dateTime(o.expires_at)}</dd>
            <dt>Thanh toán</dt><dd>{o.paid_at ? <>{dateTime(o.paid_at)} · {vnd(o.paid_amount_vnd)} · {o.provider}{r.data.order.confirmed_by_email ? ` · xác nhận bởi ${r.data.order.confirmed_by_email}` : ""}</> : "—"}</dd>
            {o.note && <><dt>Ghi chú</dt><dd>{o.note}</dd></>}
          </dl>
        </div>
        <div className="card">
          <h3>Người mua & tài khoản nhận</h3>
          {user && <dl className="kv">
            <dt>Người mua</dt><dd><Link to={`/admin/nguoi-dung/${user.id}`}>{user.email}</Link> · {user.display_name}</dd>
            <dt>Gói hiện tại</dt><dd><span className={"badge " + (user.plan === "PRO" ? "ok" : "")}>{user.plan}</span></dd>
          </dl>}
          <h4 className="mt">Tài khoản nhận (chốt khi tạo đơn)</h4>
          {b ? <dl className="kv">
            <dt>Ngân hàng</dt><dd>{b.bank_name || "—"} (BIN {b.bank_bin})</dd>
            <dt>Số tài khoản</dt><dd className="mono">{b.account_number}</dd>
            <dt>Chủ tài khoản</dt><dd>{b.account_name || <span className="muted">chưa cấu hình</span>}</dd>
          </dl> : <div className="muted small">Đơn tạo trước khi lưu thông tin tài khoản nhận.</div>}
          <h4 className="mt">Quyền đã cấp</h4>
          {subscription ? <div>Gói Pro {dateOnly(subscription.starts_at)} → {dateOnly(subscription.expires_at)} <span className="badge">{subscription.state}</span></div>
            : entitlement ? <div>{entitlement.note} · đã dùng {entitlement.attempts_used}/{entitlement.attempts_total ?? "∞"} · <span className="badge">{entitlement.status}</span></div>
            : <div className="muted small">Chưa cấp quyền nào.</div>}
        </div>
      </div>
      <div className="grid cols-2 mt">
        <div className="card">
          <h3>Lịch sử trạng thái</h3>
          <ul className="timeline">{events.map((e, i) => (
            <li key={i}>
              <div className="small muted">{dateTime(e.at)} · {SRC_LABEL[e.source] || e.source}{e.actor ? ` · ${e.actor}` : ""}</div>
              <div>{e.to ? <>{e.from ? <><OrderBadge status={e.from} /> → </> : "Tạo đơn → "}<OrderBadge status={e.to} /></> : <strong>Ghi chú</strong>}</div>
              {e.note && <div className="small">{e.note}</div>}
            </li>
          ))}</ul>
        </div>
        <div className="card">
          <h3>Giao dịch ngân hàng liên quan</h3>
          {!transactions.length ? <div className="muted small">Chưa có giao dịch nào khớp với đơn này.</div> : (
            <div className="table-wrap"><table className="data">
              <thead><tr><th>Kênh</th><th className="num">Số tiền</th><th>Nội dung</th><th>Kết quả</th><th>Nhận lúc</th></tr></thead>
              <tbody>{transactions.map((t) => (
                <tr key={t.id}><td>{t.provider}<div className="small muted mono">{t.provider_txn_id}</div></td><td className="num">{vnd(t.amount_vnd)}</td>
                  <td className="small">{t.content}</td><td><span className="badge">{t.status}</span>{t.note && <div className="small muted">{t.note}</div>}</td><td className="small">{dateTime(t.received_at)}</td></tr>
              ))}</tbody>
            </table></div>
          )}
        </div>
      </div>
      {act && (
        <Confirm title={`${ACT[act][1]} – ${o.code}`} danger={ACT[act][2]} confirmText={ACT[act][0]} busy={busy}
                 onConfirm={() => { if (needsNote && note.trim().length < 3) { toast("Vui lòng nhập ghi chú (≥ 3 ký tự).", "error"); return; } run(); }}
                 onClose={() => setAct(null)}>
          <div className="stack">
            {act === "confirm" && <div className="alert warn">Chỉ xác nhận khi đã thấy khoản tiền {vnd(o.amount_vnd)} với nội dung <strong className="mono">{o.transfer_content}</strong> trong tài khoản nhận.
              Hệ thống cấp {o.kind === "pro" ? "gói Pro" : "lượt thi"} ngay, chỉ một lần cho mỗi đơn, và ghi lại người xác nhận.</div>}
            {act === "refund" && <div className="alert warn">Quyền đã cấp từ đơn này bị thu hồi. Việc chuyển trả tiền thực hiện ngoài hệ thống.</div>}
            {act === "fail" && <div className="alert warn">Đơn chuyển sang “Thất bại” (không cấp quyền). Dùng khi giao dịch bị hoàn/sai hoặc khách không thanh toán.</div>}
            {act === "confirm" && <div className="field"><label htmlFor="oa-amt">Số tiền thực nhận (VND)</label>
              <input id="oa-amt" className="input" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} /></div>}
            <div className="field"><label htmlFor="oa-note">Ghi chú / mã giao dịch ngân hàng{needsNote ? " (bắt buộc)" : ""}</label>
              <input id="oa-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="vd: MB app FT2627..., khách gửi ảnh chụp" /></div>
          </div>
        </Confirm>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ plans
interface APlan {
  code: string; name: string; description: string | null; price_vnd: number; duration_days: number | null; is_active: boolean;
  benefits: string[]; updated_at: string | null; updated_by: string | null; active_subscribers: number | null;
}

export function Plans() {
  const r = useAsync(() => get<{ items: APlan[]; free_questions_per_subject: number }>("/api/admin/plans"), []);
  const [edit, setEdit] = useState<(Omit<APlan, "price_vnd" | "duration_days" | "benefits"> & { price: string; days: string; benefits: string }) | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    if (!edit) return;
    setBusy(true);
    try {
      await put(`/api/admin/plans/${edit.code}`, {
        name: edit.name, description: edit.description, price_vnd: Number(edit.price || 0),
        duration_days: edit.code === "FREE" ? null : numOrNull(edit.days), is_active: edit.is_active,
        benefits: edit.benefits.split("\n").map((x) => x.trim()).filter(Boolean),
      });
      toast("Đã lưu gói. Đơn mới dùng giá/thời hạn mới; đơn cũ giữ nguyên.", "ok");
      setEdit(null); setConfirm(false); r.reload();
    } catch (e) { toast(errMsg(e), "error"); setConfirm(false); } finally { setBusy(false); }
  };
  if (r.loading) return <Spinner />;
  if (r.error || !r.data) return <ErrorBox error={r.error} />;
  return (
    <div>
      <PageHead title="Gói luyện tập & giá" />
      <p className="muted">Gói Miễn phí giới hạn <strong>{r.data.free_questions_per_subject}</strong> câu luyện tập mỗi môn (đổi ở <Link to="/admin/cai-dat?tab=practice">Cài đặt → Luyện tập</Link>).
        Gói Pro mở toàn bộ ngân hàng câu hỏi đủ điều kiện trong số ngày cấu hình. Đề thi thử có phí bán riêng (<Link to="/admin/de-thi">Đề thi thử</Link>).</p>
      <div className="grid cols-2">
        {r.data.items.map((p) => (
          <div key={p.code} className="card stack">
            <div className="row between"><h2 style={{ margin: 0 }}>{p.name} <span className="badge">{p.code}</span></h2>
              {p.is_active ? <span className="badge ok">Đang bán</span> : <span className="badge bad">Tạm ngừng</span>}</div>
            <div style={{ fontSize: "1.5rem", fontWeight: 800 }}>{vnd(p.code === "FREE" ? 0 : p.price_vnd)} {p.duration_days ? <span className="muted small">/ {p.duration_days} ngày</span> : null}</div>
            {p.description && <div className="muted">{p.description}</div>}
            <ul className="benefits">{p.benefits.map((x) => <li key={x}>{x}</li>)}</ul>
            {p.active_subscribers != null && <div className="small">Đang có hiệu lực: <strong>{p.active_subscribers}</strong> tài khoản</div>}
            <div className="small muted">{p.updated_at ? `Sửa lần cuối ${dateTime(p.updated_at)}${p.updated_by ? ` bởi ${p.updated_by}` : ""}` : ""}</div>
            <div><button className="btn secondary sm" onClick={() => setEdit({ ...p, price: String(p.price_vnd), days: p.duration_days ? String(p.duration_days) : "", benefits: p.benefits.join("\n") })}>Sửa gói</button></div>
          </div>
        ))}
      </div>
      {edit && !confirm && (
        <Confirm title={`Sửa ${edit.name}`} confirmText="Tiếp tục" onConfirm={() => setConfirm(true)} onClose={() => setEdit(null)}>
          <div className="stack">
            <div className="field"><label htmlFor="pl-name">Tên hiển thị</label><input id="pl-name" className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></div>
            <div className="field"><label htmlFor="pl-desc">Mô tả</label><input id="pl-desc" className="input" value={edit.description || ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></div>
            {edit.code !== "FREE" && <div className="grid cols-2">
              <div className="field"><label htmlFor="pl-price">Giá (VND)</label><input id="pl-price" className="input" type="number" min={1000} value={edit.price} onChange={(e) => setEdit({ ...edit, price: e.target.value })} /></div>
              <div className="field"><label htmlFor="pl-days">Thời hạn (ngày)</label>
                <input id="pl-days" className="input" type="number" min={1} value={edit.days} onChange={(e) => setEdit({ ...edit, days: e.target.value })} />
                <div className="seg mt">{[30, 90, 180, 365].map((n) => <button type="button" key={n} className={edit.days === String(n) ? "on" : ""} onClick={() => setEdit({ ...edit, days: String(n) })}>{n}</button>)}</div></div>
            </div>}
            <div className="field"><label htmlFor="pl-ben">Quyền lợi hiển thị (mỗi dòng một ý)</label>
              <textarea id="pl-ben" className="input" rows={4} value={edit.benefits} onChange={(e) => setEdit({ ...edit, benefits: e.target.value })} /></div>
            <label className="check"><input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /> Đang bán / hiển thị</label>
          </div>
        </Confirm>
      )}
      {edit && confirm && (
        <Confirm title="Lưu thay đổi gói?" confirmText="Lưu" busy={busy} onConfirm={save} onClose={() => setConfirm(false)}>
          <p>{edit.name}: {edit.code === "FREE" ? "" : `${vnd(Number(edit.price || 0))} / ${edit.days || "?"} ngày`}{edit.is_active ? "" : " · tạm ngừng bán"}.</p>
          <p className="muted small">Áp dụng cho đơn hàng tạo từ bây giờ. Đơn đã tạo giữ nguyên số tiền và thời hạn; gói Pro đang hiệu lực không thay đổi. Thay đổi được ghi vào Nhật ký.</p>
        </Confirm>
      )}
    </div>
  );
}
