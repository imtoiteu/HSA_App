import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, patch, post, qs } from "../../lib/api";
import { dateTime, num, ORDER_STATUS, vnd } from "../../lib/format";
import { Confirm, Empty, ErrorBox, Pager, Spinner, useAsync, useToast } from "../../components/ui";
import { copyText, errMsg, numOrNull, PageHead, useUrlState } from "./common";

interface UserRow { id: number; email: string; display_name: string; role: string; created_at: string; is_active: boolean; last_login_at: string | null; sessions?: number }

export function Users() {
  const u = useUrlState();
  const nav = useNavigate();
  const page = Number(u.get("page") || 1);
  const [q, setQ] = useState(u.get("q"));
  const list = useAsync(() => get<{ total: number; items: UserRow[] }>("/api/admin/users" + qs({ q: u.get("q"), role: u.get("role"), page, size: 50 })),
    [u.get("q"), u.get("role"), page]);
  return (
    <div>
      <PageHead title="Người dùng" />
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); u.set({ q }); }}>
        <div className="field" style={{ minWidth: 260 }}><label htmlFor="u-q">Email hoặc tên</label>
          <input id="u-q" className="input" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <div className="field"><label htmlFor="u-role">Vai trò</label>
          <select id="u-role" className="input" value={u.get("role")} onChange={(e) => u.set({ role: e.target.value })}>
            <option value="">Tất cả</option><option value="student">Học sinh</option><option value="admin">Quản trị</option>
          </select></div>
        <button className="btn" type="submit">Tìm</button>
      </form>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Không có người dùng." /> : (
        <>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>#</th><th>Email</th><th>Tên</th><th>Vai trò</th><th>Trạng thái</th><th>Bài làm</th><th>Đăng nhập gần nhất</th><th>Tạo lúc</th></tr></thead>
            <tbody>{list.data.items.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => nav(`/admin/nguoi-dung/${r.id}`)}>
                <td>{r.id}</td><td>{r.email}</td><td>{r.display_name}</td>
                <td>{r.role === "admin" ? <span className="badge brand">Quản trị</span> : "Học sinh"}</td>
                <td>{r.is_active ? <span className="badge ok">Hoạt động</span> : <span className="badge bad">Đã khoá</span>}</td>
                <td>{num(r.sessions ?? 0, 0)}</td>
                <td className="small nowrap">{dateTime(r.last_login_at)}</td><td className="small nowrap">{dateTime(r.created_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pager page={page} size={50} total={list.data.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
    </div>
  );
}

interface Ent { id: number; note: string | null; blueprint_ids: number[]; attempts_total: number | null; attempts_used: number; valid_until: string | null; status: string }
interface UOrder { id: number; code: string; status: string; amount_vnd: number; name: string; created_at: string; paid_at: string | null }
interface USession { id: string; title: string; mode: string; status: string; started_at: string; total: number; answered: number; score: number | null; max_score: number | null }
interface UDetail { user: UserRow; entitlements: Ent[]; orders: UOrder[]; sessions: USession[] }

export function UserDetail() {
  const { id } = useParams();
  const d = useAsync(() => get<UDetail>(`/api/admin/users/${id}`), [id]);
  const bps = useAsync(() => get<{ items: { id: number; name: string; price_vnd: number }[] }>("/api/admin/blueprints"), []);
  const [link, setLink] = useState("");
  const [grant, setGrant] = useState({ blueprint_ids: [] as number[], attempts: "1", days: "", note: "" });
  const [confirm, setConfirm] = useState<null | { kind: "role" | "active" | "revoke" | "grant"; eid?: number }>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  if (d.loading) return <Spinner />;
  if (d.error || !d.data) return <ErrorBox error={d.error} />;
  const { user, entitlements, orders, sessions } = d.data;
  const bpName = (bid: number) => bps.data?.items.find((b) => b.id === bid)?.name || `#${bid}`;

  const run = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      if (confirm.kind === "role") await patch(`/api/admin/users/${user.id}`, { role: user.role === "admin" ? "student" : "admin" });
      if (confirm.kind === "active") await patch(`/api/admin/users/${user.id}`, { is_active: !user.is_active });
      if (confirm.kind === "revoke") await post(`/api/admin/entitlements/${confirm.eid}/revoke`);
      if (confirm.kind === "grant") await post(`/api/admin/users/${user.id}/grant`, {
        blueprint_ids: grant.blueprint_ids, attempts: numOrNull(grant.attempts), days: numOrNull(grant.days), note: grant.note || null });
      toast("Đã cập nhật.", "ok"); setConfirm(null); d.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const resetLink = async () => {
    try {
      const r = await post<{ link: string }>(`/api/admin/users/${user.id}/reset-link`);
      setLink(r.link);
    } catch (e) { toast(errMsg(e), "error"); }
  };

  return (
    <div className="stack">
      <div className="small"><Link to="/admin/nguoi-dung">← Danh sách người dùng</Link></div>
      <PageHead title={user.display_name}>
        {user.role === "admin" && <span className="badge brand">Quản trị</span>}
        {!user.is_active && <span className="badge bad">Đã khoá</span>}
      </PageHead>
      <div className="grid cols-2">
        <div className="card">
          <h3>Tài khoản</h3>
          <dl className="kv">
            <dt>Email</dt><dd>{user.email}</dd>
            <dt>Tạo lúc</dt><dd>{dateTime(user.created_at)}</dd>
            <dt>Đăng nhập gần nhất</dt><dd>{dateTime(user.last_login_at)}</dd>
          </dl>
          <div className="row mt">
            <button className="btn secondary sm" onClick={() => setConfirm({ kind: "role" })}>{user.role === "admin" ? "Hạ xuống học sinh" : "Cấp quyền quản trị"}</button>
            <button className={"btn sm " + (user.is_active ? "danger" : "ok")} onClick={() => setConfirm({ kind: "active" })}>{user.is_active ? "Khoá tài khoản" : "Mở khoá"}</button>
            <button className="btn secondary sm" onClick={resetLink}>Tạo link đặt lại mật khẩu</button>
          </div>
          {link && (
            <div className="alert info mt small">
              <div>Link có hiệu lực 24 giờ — gửi riêng cho người dùng:</div>
              <div className="row mt"><input className="input mono" readOnly value={link} aria-label="Link đặt lại mật khẩu" onFocus={(e) => e.target.select()} />
                <button className="btn sm" onClick={() => { copyText(link); toast("Đã sao chép.", "ok"); }}>Sao chép</button></div>
            </div>
          )}
        </div>
        <div className="card">
          <h3>Cấp quyền làm đề</h3>
          <div className="stack">
            <div className="field"><span className="label">Đề áp dụng (không chọn = mọi đề có phí)</span>
              <div className="chip-group">
                {bps.data?.items.filter((b) => b.price_vnd > 0).map((b) => {
                  const on = grant.blueprint_ids.includes(b.id);
                  return <button key={b.id} type="button" className={"chip" + (on ? " on" : "")} aria-pressed={on}
                                 onClick={() => setGrant({ ...grant, blueprint_ids: on ? grant.blueprint_ids.filter((x) => x !== b.id) : [...grant.blueprint_ids, b.id] })}>{b.name}</button>;
                })}
              </div></div>
            <div className="row">
              <div className="field grow"><label htmlFor="g-att">Số lượt (trống = không giới hạn)</label>
                <input id="g-att" className="input" type="number" min={1} value={grant.attempts} onChange={(e) => setGrant({ ...grant, attempts: e.target.value })} /></div>
              <div className="field grow"><label htmlFor="g-days">Số ngày (trống = vĩnh viễn)</label>
                <input id="g-days" className="input" type="number" min={1} value={grant.days} onChange={(e) => setGrant({ ...grant, days: e.target.value })} /></div>
            </div>
            <div className="field"><label htmlFor="g-note">Ghi chú</label>
              <input id="g-note" className="input" value={grant.note} onChange={(e) => setGrant({ ...grant, note: e.target.value })} placeholder="vd: khuyến mãi, bù lỗi" /></div>
            <div><button className="btn" onClick={() => setConfirm({ kind: "grant" })}>Cấp quyền</button></div>
          </div>
        </div>
      </div>
      <div className="card">
        <h3>Quyền đã có</h3>
        {!entitlements.length ? <div className="muted small">Chưa có.</div> : (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>#</th><th>Mô tả</th><th>Đề</th><th>Lượt</th><th>Hết hạn</th><th>Trạng thái</th><th /></tr></thead>
            <tbody>{entitlements.map((e) => (
              <tr key={e.id}><td>{e.id}</td><td>{e.note}</td>
                <td className="small">{e.blueprint_ids.length ? e.blueprint_ids.map(bpName).join(", ") : "Mọi đề có phí"}</td>
                <td>{e.attempts_used}/{e.attempts_total ?? "∞"}</td><td className="small">{e.valid_until ? dateTime(e.valid_until) : "Vĩnh viễn"}</td>
                <td><span className={"badge " + (e.status === "active" ? "ok" : "bad")}>{e.status}</span></td>
                <td>{e.status === "active" && <button className="btn secondary sm" onClick={() => setConfirm({ kind: "revoke", eid: e.id })}>Thu hồi</button>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h3>Đơn hàng</h3>
          {!orders.length ? <div className="muted small">Chưa có.</div> : (
            <div className="table-wrap"><table className="data"><tbody>{orders.map((o) => (
              <tr key={o.id}><td className="mono">{o.code}</td><td className="small">{o.name}</td><td>{vnd(o.amount_vnd)}</td>
                <td><span className={"badge " + (ORDER_STATUS[o.status]?.[1] || "")}>{ORDER_STATUS[o.status]?.[0] || o.status}</span></td>
                <td className="small">{dateTime(o.created_at)}</td></tr>
            ))}</tbody></table></div>
          )}
        </div>
        <div className="card">
          <h3>Bài làm gần đây</h3>
          {!sessions.length ? <div className="muted small">Chưa có.</div> : (
            <div className="table-wrap"><table className="data"><tbody>{sessions.map((s) => (
              <tr key={s.id}>
                <td>{s.status === "submitted" ? <Link to={`/ket-qua/${s.id}`}>{s.title}</Link> : s.title}
                  <div className="small muted">{s.mode === "exam" ? "Thi thử" : "Luyện tập"} · {dateTime(s.started_at)}</div></td>
                <td className="small">{s.status}</td>
                <td className="small">{s.score != null ? `${num(s.score)}/${num(s.max_score)}` : `${s.answered}/${s.total}`}</td>
              </tr>
            ))}</tbody></table></div>
          )}
        </div>
      </div>
      {confirm && (
        <Confirm title="Xác nhận" danger={confirm.kind === "revoke" || (confirm.kind === "active" && user.is_active)} busy={busy} onConfirm={run} onClose={() => setConfirm(null)}>
          {confirm.kind === "role" && (user.role === "admin" ? "Hạ người dùng này xuống vai trò học sinh?" : "Cấp toàn quyền quản trị cho người dùng này?")}
          {confirm.kind === "active" && (user.is_active ? "Khoá tài khoản sẽ đăng xuất mọi phiên của người dùng." : "Mở khoá tài khoản này?")}
          {confirm.kind === "revoke" && "Thu hồi quyền này? Các lượt chưa dùng sẽ mất."}
          {confirm.kind === "grant" && `Cấp ${grant.attempts || "không giới hạn"} lượt${grant.days ? ` trong ${grant.days} ngày` : ""} cho ${grant.blueprint_ids.length ? grant.blueprint_ids.map(bpName).join(", ") : "mọi đề có phí"}?`}
        </Confirm>
      )}
    </div>
  );
}
