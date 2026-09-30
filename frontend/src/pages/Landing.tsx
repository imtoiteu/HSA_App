import { Link, Navigate } from "react-router-dom";
import { RichContent } from "../components/RichContent";
import { useAsync } from "../components/ui";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Block, Catalog } from "../lib/types";

const SAMPLE: Block[] = [
  { t: "p", c: [{ t: "s", v: "Cho hàm số " }, { t: "m", tex: "f(x)=\\dfrac{x^2+1}{x-1}" }, { t: "s", v: ". Tìm giá trị nhỏ nhất của " },
    { t: "m", tex: "f(x)" }, { t: "s", v: " trên khoảng " }, { t: "m", tex: "(1;+\\infty)" }, { t: "s", v: "." }] },
];

export default function Landing() {
  const { user, ready } = useAuth();
  const { data } = useAsync(() => get<Catalog>("/api/catalog"), []);
  if (ready && user) return <Navigate to="/tong-quan" replace />;
  return (
    <>
      <section className="hero">
        <div className="container">
          <span className="badge brand mb">Kỳ thi Đánh giá năng lực ĐHQG Hà Nội</span>
          <h1>Luyện thi <span className="accent-text">HSA</span> thông minh, sát đề, chấm điểm tức thì</h1>
          <p className="lead">Ngân hàng câu hỏi được biên tập kỹ lưỡng, công thức hiển thị chuẩn, đề thi thử mô phỏng đúng cấu trúc 3 phần thi — kèm đáp án và lời giải chi tiết.</p>
          <div className="row">
            <Link to="/dang-ky" className="btn lg">Bắt đầu miễn phí</Link>
            <Link to="/de-thi" className="btn lg secondary">Xem đề thi thử</Link>
          </div>
          {data && (
            <div className="row mt-lg small muted" style={{ gap: 24 }}>
              <span><strong style={{ color: "var(--ink)", fontSize: "1.2rem" }}>{data.served_total.toLocaleString("vi-VN")}</strong> câu hỏi đã kiểm duyệt</span>
              <span><strong style={{ color: "var(--ink)", fontSize: "1.2rem" }}>{data.blueprints.length}</strong> dạng đề thi thử</span>
              <span><strong style={{ color: "var(--ink)", fontSize: "1.2rem" }}>{data.subjects.filter((s) => s.available > 0).length}</strong> môn học</span>
            </div>
          )}
        </div>
      </section>
      <section className="container page">
        <div className="grid cols-3">
          {[
            ["🎯", "Đề thi thử chuẩn cấu trúc", "Toán học & Xử lý số liệu, Văn học – Ngôn ngữ, Khoa học hoặc Tiếng Anh; tính giờ riêng từng phần như thi thật."],
            ["⚡", "Luyện tập linh hoạt", "Chọn môn, dạng câu, số câu, thời gian. Xem đáp án ngay hoặc sau khi nộp bài."],
            ["🧮", "Công thức & hình vẽ chuẩn", "Công thức Toán, Lý, Hoá hiển thị sắc nét trên mọi thiết bị; hình và bảng giữ nguyên như đề gốc."],
            ["📊", "Chấm điểm & phân tích", "Điểm từng phần, câu đúng/sai/bỏ trống, tỉ lệ đúng theo môn để biết cần ôn gì."],
            ["🔖", "Lưu câu & ôn câu sai", "Lưu câu hay, tự động tổng hợp câu làm sai để luyện lại."],
            ["💾", "Tự động lưu bài", "Mất mạng hay lỡ tải lại trang? Bài làm vẫn được giữ nguyên, làm tiếp bất cứ lúc nào."],
          ].map(([ic, t, d]) => (
            <div key={t} className="card feature">
              <div className="ic" aria-hidden>{ic}</div>
              <h3>{t}</h3>
              <p className="muted small" style={{ margin: 0 }}>{d}</p>
            </div>
          ))}
        </div>
        <div className="card pad-lg mt-lg">
          <div className="muted small mb">Ví dụ hiển thị câu hỏi</div>
          <RichContent blocks={SAMPLE} />
        </div>
      </section>
    </>
  );
}
