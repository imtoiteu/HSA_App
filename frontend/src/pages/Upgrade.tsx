import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ErrorBox, Spinner, useAsync } from "../components/ui";
import { ApiError, get, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateOnly, vnd } from "../lib/format";
import type { MyPlan, Order, PlanInfo } from "../lib/types";

export default function Upgrade() {
  const { user } = useAuth();
  const nav = useNavigate();
  const plans = useAsync(() => get<{ items: PlanInfo[]; free_questions_per_subject: number }>("/api/plans"), []);
  const mine = useAsync(() => (user ? get<MyPlan>("/api/me/plan") : Promise.resolve(null)), [user?.id]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  if (plans.loading || mine.loading) return <Spinner />;
  if (plans.error || !plans.data) return <div className="container page"><ErrorBox error={plans.error} /></div>;
  const free = plans.data.items.find((p) => p.code === "FREE");
  const pro = plans.data.items.find((p) => p.code === "PRO");
  const limit = plans.data.free_questions_per_subject;
  const me = mine.data;
  const isPro = me?.plan === "PRO";

  const buy = async () => {
    setBusy(true);
    setErr(null);
    try {
      const o = await post<Order>("/api/orders", { plan_code: "PRO" });
      nav(`/thanh-toan/${o.code}`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <div className="container page">
      <h1>Gói luyện tập</h1>
      <p className="muted">Gói luyện tập áp dụng cho phần Luyện tập. Đề thi thử có phí được mua riêng theo từng đề.</p>

      {me && (
        <div className={"card mb plan-status " + (isPro ? "pro" : "")}>
          <div className="row between" style={{ flexWrap: "wrap", gap: 12 }}>
            <div>
              <div className="muted small">Gói hiện tại</div>
              <div style={{ fontSize: "1.25rem", fontWeight: 700 }}>
                {me.plan === "PRO" ? "Gói Pro" : me.plan === "ADMIN" ? "Quản trị viên (toàn quyền)" : "Miễn phí"}
              </div>
              {isPro && me.pro_until && (
                <div className="small">Hiệu lực đến <strong>{dateOnly(me.pro_until)}</strong> · còn {me.days_left} ngày</div>
              )}
              {!isPro && me.expired_at && (
                <div className="small muted">Gói Pro của bạn đã hết hạn ngày {dateOnly(me.expired_at)}. Lịch sử làm bài vẫn được giữ nguyên.</div>
              )}
            </div>
            <Link to="/tai-khoan" className="btn ghost sm">Lịch sử thanh toán</Link>
          </div>
        </div>
      )}

      <div className="grid cols-2 plan-grid">
        {free && (
          <div className="card pad-lg stack">
            <div className="row between"><h2 style={{ margin: 0 }}>{free.name}</h2>{me?.plan === "FREE" && <span className="badge">Đang dùng</span>}</div>
            <div className="plan-price">{vnd(0)}</div>
            <ul className="benefits">
              <li><strong>{limit.toLocaleString("vi-VN")} câu</strong> luyện tập mỗi môn (cố định, đã kiểm duyệt)</li>
              {free.benefits.map((b) => <li key={b}>{b}</li>)}
            </ul>
          </div>
        )}
        {pro && (
          <div className="card pad-lg stack plan-pro">
            <div className="row between"><h2 style={{ margin: 0 }}>{pro.name}</h2>{isPro && <span className="badge ok">Đang dùng</span>}</div>
            <div className="plan-price">{vnd(pro.price_vnd)} <span className="muted small">/ {pro.duration_days} ngày</span></div>
            {pro.description && <p className="muted" style={{ margin: 0 }}>{pro.description}</p>}
            <ul className="benefits">
              {pro.benefits.map((b) => <li key={b}>{b}</li>)}
            </ul>
            {!user ? (
              <Link className="btn accent lg" to="/dang-nhap?next=/nang-cap">Đăng nhập để nâng cấp</Link>
            ) : me?.plan === "ADMIN" ? null : (
              <button className="btn accent lg" onClick={buy} disabled={busy}>
                {busy ? "Đang tạo đơn…" : isPro ? `Gia hạn thêm ${pro.duration_days} ngày – ${vnd(pro.price_vnd)}` : `Nâng cấp Pro – ${vnd(pro.price_vnd)}`}
              </button>
            )}
            {isPro && <div className="muted small">Gia hạn được cộng nối tiếp sau ngày hết hạn hiện tại, không mất ngày đã mua.</div>}
            {err instanceof ApiError && err.code === "payment_not_configured"
              ? <div className="alert warn">Thanh toán chuyển khoản đang được thiết lập. Vui lòng quay lại sau hoặc liên hệ hỗ trợ.</div>
              : <ErrorBox error={err} />}
          </div>
        )}
        {!pro && <div className="card pad-lg empty">Gói Pro hiện chưa mở bán.</div>}
      </div>

      <div className="card mt">
        <h3>Thanh toán thế nào?</h3>
        <ol className="small" style={{ paddingLeft: 18, margin: 0 }}>
          <li>Bấm “Nâng cấp Pro” để tạo đơn hàng với số tiền và nội dung chuyển khoản riêng.</li>
          <li>Quét mã VietQR hoặc chuyển khoản đúng số tiền và nội dung.</li>
          <li>Gói Pro được kích hoạt ngay khi giao dịch được xác nhận; nếu chưa tự động, quản trị viên sẽ đối soát thủ công.</li>
        </ol>
        <p className="muted small" style={{ marginBottom: 0 }}>Khi gói Pro hết hạn, tài khoản trở về gói Miễn phí; lịch sử, kết quả và câu đã lưu không bị xoá.</p>
      </div>
    </div>
  );
}
