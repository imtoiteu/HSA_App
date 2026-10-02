import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ErrorBox, Modal, Spinner, useAsync } from "../components/ui";
import { ApiError, get, post } from "../lib/api";
import { useAuth } from "../lib/auth";
import { vnd } from "../lib/format";
import type { Blueprint, Catalog, Order, SessionSummary } from "../lib/types";

export function Price({ bp }: { bp: Blueprint }) {
  return bp.promo ? <><s className="muted" style={{ fontWeight: 400 }}>{vnd(bp.list_price_vnd)}</s> {vnd(bp.price_vnd)}</> : <>{vnd(bp.price_vnd)}</>;
}

export function ExamCard({ bp, onOpen }: { bp: Blueprint; onOpen: (bp: Blueprint) => void }) {
  const acc = bp.access;
  return (
    <button className="card card-link" style={{ textAlign: "left", cursor: "pointer" }} onClick={() => onOpen(bp)}>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <h3 style={{ margin: 0 }}>{bp.name}</h3>
        {bp.paid ? (
          acc.allowed ? <span className="badge ok">{acc.admin ? "Quản trị" : acc.via_pro ? "Gói Pro" : acc.unlimited ? "Không giới hạn" : `Còn ${acc.remaining_attempts} lượt`}</span>
            : <span className="badge accent"><Price bp={bp} />{bp.attempts_per_purchase > 1 ? ` / ${bp.attempts_per_purchase} lượt` : "/lượt"}</span>
        ) : <span className="badge brand">Miễn phí</span>}
      </div>
      <p className="muted small mt" style={{ minHeight: 40 }}>{bp.description}</p>
      <div className="row small" style={{ gap: 16, color: "var(--ink-2)" }}>
        <span>📝 {bp.total_questions} câu</span>
        {bp.total_minutes && <span>⏱ {bp.total_minutes} phút</span>}
        {bp.sections.length > 1 && <span>📚 {bp.sections.length} phần</span>}
      </div>
    </button>
  );
}

export default function Exams() {
  const { data, error, loading, reload } = useAsync(() => get<Catalog>("/api/catalog"), []);
  const [open, setOpen] = useState<Blueprint | null>(null);
  if (loading) return <Spinner />;
  if (error || !data) return <div className="container page"><ErrorBox error={error} /></div>;
  return (
    <div className="container page">
      <h1>Đề thi thử</h1>
      <p className="muted">Mỗi lượt làm bài được tạo ngẫu nhiên từ ngân hàng câu hỏi đã kiểm duyệt theo cấu trúc đề, có tính giờ và chấm điểm tự động.</p>
      {data.blueprints.length === 0 ? <div className="empty">Chưa có đề thi nào được mở.</div> : (
        <div className="grid cols-2 mt">
          {data.blueprints.map((bp) => <ExamCard key={bp.id} bp={bp} onOpen={setOpen} />)}
        </div>
      )}
      {open && <StartExam bp={open} onClose={() => { setOpen(null); reload(); }} />}
    </div>
  );
}

export function StartExam({ bp, onClose }: { bp: Blueprint; onClose: () => void }) {
  const { user } = useAuth();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const needsPay = bp.paid && !bp.access.allowed;
  const purchase = bp.attempts_per_purchase > 1 ? `${bp.attempts_per_purchase} lượt` : "1 lượt";

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ session: SessionSummary; resumed: boolean }>("/api/sessions", { blueprint_id: bp.id });
      nav(`/lam-bai/${r.session.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "payment_required") return buy();
      setErr(e);
      setBusy(false);
    }
  };
  const buy = async () => {
    setBusy(true);
    try {
      const o = await post<Order>("/api/orders", { blueprint_id: bp.id });
      nav(`/thanh-toan/${o.code}`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <Modal title={bp.name} onClose={onClose} actions={
      !user ? <>
        <Link className="btn secondary" to="/dang-ky">Đăng ký</Link>
        <Link className="btn" to={`/dang-nhap?next=/de-thi`}>Đăng nhập để làm bài</Link>
      </> : needsPay ? <>
        <button className="btn secondary" onClick={onClose}>Để sau</button>
        <button className="btn accent" onClick={buy} disabled={busy}>{busy ? "Đang tạo đơn…" : `Mua ${purchase} – ${vnd(bp.price_vnd)}`}</button>
      </> : <>
        <button className="btn secondary" onClick={onClose}>Huỷ</button>
        <button className="btn" onClick={start} disabled={busy}>{busy ? "Đang chuẩn bị đề…" : "Bắt đầu làm bài"}</button>
      </>}>
      {bp.description && <p>{bp.description}</p>}
      <div className="table-wrap">
        <table className="data">
          <thead><tr><th>Phần</th><th>Số câu</th><th>Thời gian</th></tr></thead>
          <tbody>
            {bp.sections.map((s) => (
              <tr key={s.key}><td>{s.title}</td><td>{s.count}</td><td>{s.duration_minutes ? `${s.duration_minutes} phút` : bp.sections.length === 1 && bp.total_minutes ? `${bp.total_minutes} phút` : "—"}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="small muted mt" style={{ paddingLeft: 18 }}>
        {bp.timing === "per_section" && <li>Các phần thi làm lần lượt; mỗi phần có thời gian riêng, nộp phần trước mới sang phần sau.</li>}
        {bp.timing === "global" && <li>Đồng hồ đếm ngược bắt đầu ngay khi vào bài và vẫn chạy nếu bạn tạm rời đi.</li>}
        <li>Câu trả lời được lưu tự động; bạn có thể tải lại trang mà không mất bài.</li>
        <li>Hết giờ, bài được nộp tự động. Đáp án và lời giải hiển thị sau khi nộp.</li>
        {bp.paid && <li>Đề có phí: mỗi lần mua {vnd(bp.price_vnd)} được {purchase} làm bài. Mỗi lần bắt đầu dùng 1 lượt; nếu đang làm dở, bạn được tiếp tục bài cũ, không mất thêm lượt. Đề thi thử được mua riêng, không thuộc gói luyện tập Pro{bp.access.via_pro ? " (hiện gói Pro của bạn đã bao gồm đề này)" : ""}.</li>}
      </ul>
      <ErrorBox error={err} />
    </Modal>
  );
}
