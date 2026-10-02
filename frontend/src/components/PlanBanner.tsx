import { Link } from "react-router-dom";
import { dateOnly, vnd } from "../lib/format";
import type { AccessInfo } from "../lib/types";

/** One-line status of the practice plan: FREE (limit + upgrade link) or PRO (valid until). */
export function PlanBanner({ access, compact }: { access: AccessInfo; compact?: boolean }) {
  if (access.plan === "ADMIN") return null;
  if (access.plan === "PRO") {
    return (
      <div className="plan-banner pro">
        <span className="badge ok">Gói Pro</span>
        <span>Bạn được luyện tập toàn bộ ngân hàng câu hỏi đủ điều kiện{access.pro_until ? <> · hiệu lực đến <strong>{dateOnly(access.pro_until)}</strong></> : null}.</span>
        {!compact && <Link to="/nang-cap" className="btn ghost sm">Gia hạn</Link>}
      </div>
    );
  }
  return (
    <div className="plan-banner free">
      <div>
        <strong>Bạn đang sử dụng gói Miễn phí</strong>
        <div className="small">
          Truy cập {access.free_limit.toLocaleString("vi-VN")} câu luyện tập của mỗi môn.
          {access.pro_available && <> Nâng cấp Pro để truy cập toàn bộ ngân hàng câu hỏi đủ điều kiện.</>}
        </div>
      </div>
      {access.pro_available && (
        <Link to="/nang-cap" className="btn accent sm">
          Nâng cấp Pro{access.pro_price_vnd ? ` · ${vnd(access.pro_price_vnd)}` : ""}
        </Link>
      )}
    </div>
  );
}

/** Shown instead of an error when a FREE account reaches content outside its pool. */
export function UpgradeState({ limit, message }: { limit?: number; message?: string }) {
  return (
    <div className="card pad-lg center upgrade-state" role="status">
      <div style={{ fontSize: "2rem" }} aria-hidden>🔒</div>
      <h3 style={{ marginTop: 4 }}>Bạn đã dùng hết câu luyện tập miễn phí phù hợp</h3>
      <p className="muted">
        {message || `Gói Miễn phí cho phép luyện ${limit ?? ""} câu mỗi môn.`} Lịch sử, kết quả và câu đã lưu của bạn vẫn được giữ nguyên.
      </p>
      <div className="row" style={{ justifyContent: "center" }}>
        <Link to="/nang-cap" className="btn accent">Xem gói Pro</Link>
      </div>
      <p className="muted small mt">Bạn vẫn có thể chọn “Ngẫu nhiên” để luyện lại các câu miễn phí.</p>
    </div>
  );
}
