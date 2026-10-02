import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ErrorBox, Spinner, useAsync, useToast } from "../components/ui";
import { get, patch, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateOnly, dateTime, ORDER_STATUS, vnd } from "../lib/format";
import type { MyPlan, Order } from "../lib/types";

const SUB_STATE: Record<string, [string, string]> = {
  active: ["Đang hiệu lực", "ok"], scheduled: ["Chờ đến lượt", "brand"], expired: ["Đã hết hạn", ""], revoked: ["Đã thu hồi", "bad"],
};

interface Ent { id: number; note: string | null; attempts_total: number | null; attempts_used: number; valid_until: string | null; status: string }

export default function Account() {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const orders = useAsync(() => get<{ items: Order[] }>("/api/orders"), []);
  const plan = useAsync(() => get<MyPlan>("/api/me/plan"), []);
  const ents = useAsync(() => get<{ items: Ent[] }>("/api/entitlements"), []);
  const [name, setName] = useState(user?.display_name || "");
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<unknown>(null);

  const saveName = async (e: FormEvent) => {
    e.preventDefault();
    const r = await patch<{ user: any }>("/api/auth/me", { display_name: name });
    setUser(r.user);
    toast("Đã cập nhật.", "ok");
  };
  const changePw = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const r = await post<{ user: any }>("/api/auth/change-password", { current_password: cur, new_password: pw });
      setUser(r.user);
      setCur("");
      setPw("");
      toast("Đã đổi mật khẩu. Các phiên đăng nhập khác đã bị đăng xuất.", "ok");
    } catch (ex) {
      setErr(ex);
    }
  };

  return (
    <div className="container narrow page">
      <h1>Tài khoản</h1>
      {plan.data && (
        <div className={"card mb plan-status " + (plan.data.plan === "PRO" ? "pro" : "")}>
          <div className="row between" style={{ flexWrap: "wrap", gap: 12 }}>
            <div>
              <div className="muted small">Gói luyện tập</div>
              <div style={{ fontSize: "1.2rem", fontWeight: 700 }}>{plan.data.plan === "PRO" ? "Gói Pro" : plan.data.plan === "ADMIN" ? "Quản trị viên" : "Miễn phí"}</div>
              {plan.data.plan === "PRO" && plan.data.pro_until
                ? <div className="small">Hiệu lực đến <strong>{dateOnly(plan.data.pro_until)}</strong> · còn {plan.data.days_left} ngày</div>
                : plan.data.plan === "FREE" && <div className="small muted">{plan.data.free_questions_per_subject} câu luyện tập mỗi môn{plan.data.expired_at ? ` · gói Pro đã hết hạn ngày ${dateOnly(plan.data.expired_at)}` : ""}</div>}
            </div>
            {plan.data.plan !== "ADMIN" && <Link to="/nang-cap" className={"btn " + (plan.data.plan === "PRO" ? "secondary" : "accent")}>{plan.data.plan === "PRO" ? "Gia hạn" : "Nâng cấp Pro"}</Link>}
          </div>
          {plan.data.subscriptions.length > 0 && (
            <div className="table-wrap mt"><table className="data">
              <thead><tr><th>Gói</th><th>Từ</th><th>Đến</th><th>Nguồn</th><th>Trạng thái</th></tr></thead>
              <tbody>{plan.data.subscriptions.map((x) => {
                const [l, c] = SUB_STATE[x.state] || [x.state, ""];
                return (
                  <tr key={x.id}><td>{x.name || x.plan_code}</td><td>{dateOnly(x.starts_at)}</td><td>{dateOnly(x.expires_at)}</td>
                    <td>{x.source === "order" ? "Thanh toán" : "Quản trị viên cấp"}</td><td><span className={"badge " + c}>{l}</span></td></tr>
                );
              })}</tbody>
            </table></div>
          )}
        </div>
      )}
      <div className="grid cols-2">
        <form className="card stack" onSubmit={saveName}>
          <h3>Thông tin</h3>
          <div className="muted small">{user?.email}</div>
          <div className="field"><label htmlFor="dn">Tên hiển thị</label>
            <input id="dn" className="input" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} /></div>
          <button className="btn secondary">Lưu</button>
        </form>
        <form className="card stack" onSubmit={changePw}>
          <h3>Đổi mật khẩu</h3>
          <div className="field"><label htmlFor="cp">Mật khẩu hiện tại</label>
            <input id="cp" className="input" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></div>
          <div className="field"><label htmlFor="np">Mật khẩu mới</label>
            <input id="np" className="input" type="password" autoComplete="new-password" minLength={8} value={pw} onChange={(e) => setPw(e.target.value)} required /></div>
          <ErrorBox error={err} />
          <button className="btn secondary">Đổi mật khẩu</button>
        </form>
      </div>

      <h2 className="mt-lg">Lượt thi thử đã mua</h2>
      {ents.loading ? <Spinner /> : !ents.data?.items.length ? <div className="card empty">Chưa có. <Link to="/de-thi">Xem đề thi thử</Link></div> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Gói</th><th>Đã dùng</th><th>Hạn dùng</th><th>Trạng thái</th></tr></thead>
          <tbody>{ents.data.items.map((e) => (
            <tr key={e.id}><td>{e.note}</td><td>{e.attempts_used}{e.attempts_total != null ? `/${e.attempts_total}` : " (không giới hạn)"}</td>
              <td>{e.valid_until ? dateOnly(e.valid_until) : "Không thời hạn"}</td>
              <td>{e.status === "active" ? <span className="badge ok">Hiệu lực</span> : <span className="badge">Đã thu hồi</span>}</td></tr>
          ))}</tbody>
        </table></div>
      )}

      <h2 className="mt-lg">Lịch sử thanh toán</h2>
      {orders.loading ? <Spinner /> : !orders.data?.items.length ? <div className="card empty">Chưa có đơn hàng.</div> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Mã đơn</th><th>Sản phẩm</th><th>Số tiền</th><th>Ngày tạo</th><th>Trạng thái</th></tr></thead>
          <tbody>{orders.data.items.map((o) => {
            const [l, c] = ORDER_STATUS[o.status] || [o.status, ""];
            return (
              <tr key={o.code}><td className="mono"><Link to={`/thanh-toan/${o.code}`}>{o.code}</Link></td><td>{o.name}</td><td>{vnd(o.amount_vnd)}</td>
                <td>{dateTime(o.created_at)}</td><td><span className={"badge " + c}>{l}</span></td></tr>
            );
          })}</tbody>
        </table></div>
      )}
    </div>
  );
}
