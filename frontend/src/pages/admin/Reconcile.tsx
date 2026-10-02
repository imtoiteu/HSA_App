import { Link } from "react-router-dom";
import { get, qs } from "../../lib/api";
import { num } from "../../lib/format";
import { ErrorBox, Spinner, useAsync } from "../../components/ui";
import { PageHead } from "./common";

interface Row {
  subject: string; name: string; upstream_total: number; imported: number; effective: number; upstream_ready: number;
  eligible: number; served: number; free_accessible: number; pro_accessible: number; student_api: number; excluded: number;
  review_required: number; mismatch_in: number; mismatch_out: number; admin_overrides: number;
}
interface Resp { items: Row[]; totals: Omit<Row, "subject" | "name">; free_limit: number; practice_allow_self_check: boolean }

/** A count that opens the question list filtered to exactly those questions. */
function Drill({ n, f }: { n: number; f: Record<string, string | boolean> }) {
  if (!n) return <span className="muted">0</span>;
  return <Link className="drill" to={"/admin/cau-hoi" + qs({ ...f, active_bank: true })}>{num(n, 0)}</Link>;
}

export function SubjectReconciliation() {
  const r = useAsync(() => get<Resp>("/api/admin/reconciliation/subjects"), []);
  if (r.loading) return <Spinner />;
  if (r.error || !r.data) return <ErrorBox error={r.error} />;
  const { items, totals, free_limit } = r.data;
  const selfCheck = r.data.practice_allow_self_check;
  const bad = items.filter((x) => (selfCheck ? x.student_api !== x.served : x.student_api > x.served)
    || x.effective !== x.served + x.excluded || x.mismatch_out);
  return (
    <div>
      <PageHead title="Đối soát theo môn học"><button className="btn ghost sm" onClick={r.reload}>Tính lại</button></PageHead>
      <p className="muted">Mỗi môn được theo dõi từ phân loại cuối cùng của ngân hàng nguồn → câu đã nhập → môn hiệu lực trong ứng dụng → trạng thái QA →
        đủ điều kiện → số câu học sinh thực sự nhận qua API luyện tập (gói Pro = toàn bộ; gói Miễn phí = tối đa {free_limit} câu/môn). Bấm vào số để xem đúng danh sách câu hỏi.</p>
      {bad.length === 0
        ? <div className="alert ok mb">Khớp hoàn toàn: mọi môn có “đã nhập = đang phục vụ + loại trừ”, API học sinh = số câu đang phục vụ, không có câu bị chuyển môn ngoài ý muốn.</div>
        : <div className="alert warn mb">Có {bad.length} môn cần kiểm tra (dòng tô vàng).</div>}
      <div className="table-wrap"><table className="data">
        <thead><tr>
          <th>Môn</th><th className="num" title="Theo phân loại cuối của ngân hàng nguồn">Nguồn</th><th className="num">Đã nhập</th>
          <th className="num" title="Môn hiệu lực trong ứng dụng">Môn hiệu lực</th><th className="num">READY nguồn</th><th className="num">Đủ điều kiện</th>
          <th className="num">Gói Miễn phí</th><th className="num">Gói Pro</th><th className="num" title="Số câu truy vấn luyện tập thực sự trả về">API học sinh</th>
          <th className="num">Loại trừ</th><th className="num" title="SUBJECT_CLASSIFICATION_REVIEW">Cần xem lại môn</th><th className="num">Lệch môn</th>
        </tr></thead>
        <tbody>{items.map((x) => (
          <tr key={x.subject} className={bad.includes(x) ? "mismatch" : ""}>
            <td><strong>{x.name}</strong> <span className="muted small">{x.subject}</span></td>
            <td className="num"><Drill n={x.upstream_total} f={{ upstream_subject: x.subject }} /></td>
            <td className="num"><Drill n={x.imported} f={{ upstream_subject: x.subject }} /></td>
            <td className="num"><Drill n={x.effective} f={{ subject: x.subject }} /></td>
            <td className="num"><Drill n={x.upstream_ready} f={{ upstream_subject: x.subject, ready_upstream: true }} /></td>
            <td className="num"><Drill n={x.eligible} f={{ subject: x.subject, eligible: true }} /></td>
            <td className="num"><Drill n={x.free_accessible} f={{ subject: x.subject, free_pool: true }} /></td>
            <td className="num"><Drill n={x.pro_accessible} f={{ subject: x.subject, served: true }} /></td>
            <td className="num">{num(x.student_api, 0)}</td>
            <td className="num"><Drill n={x.excluded} f={{ subject: x.subject, served: false }} /></td>
            <td className="num"><Drill n={x.review_required} f={{ upstream_subject: x.subject, classification_review: true }} /></td>
            <td className="num"><Drill n={x.mismatch_out} f={{ upstream_subject: x.subject, subject_mismatch: true }} />
              {x.admin_overrides ? <div className="small muted">{x.admin_overrides} ghi đè</div> : null}</td>
          </tr>
        ))}</tbody>
        <tfoot><tr>
          <th>Tổng</th><th className="num">{num(totals.upstream_total, 0)}</th><th className="num">{num(totals.imported, 0)}</th><th className="num">{num(totals.effective, 0)}</th>
          <th className="num">{num(totals.upstream_ready, 0)}</th><th className="num">{num(totals.eligible, 0)}</th><th className="num">{num(totals.free_accessible, 0)}</th>
          <th className="num">{num(totals.pro_accessible, 0)}</th><th className="num">{num(totals.student_api, 0)}</th><th className="num">{num(totals.excluded, 0)}</th>
          <th className="num">{num(totals.review_required, 0)}</th><th className="num">{num(totals.mismatch_out, 0)}</th>
        </tr></tfoot>
      </table></div>
      <ul className="small muted mt" style={{ paddingLeft: 18 }}>
        <li>“Nguồn”, “READY nguồn”, “Cần xem lại môn” đếm theo môn cuối cùng của ngân hàng nguồn; các cột còn lại theo môn hiệu lực trong ứng dụng. Hai cách đếm chỉ khác nhau khi quản trị viên ghi đè môn (“Lệch môn”).</li>
        <li>Loại trừ = câu chưa READY ở nguồn (cần duyệt công thức/hình/đáp án…) hoặc không qua kiểm tra hiển thị của ứng dụng. Xem lý do từng câu trong danh sách câu hỏi hoặc <Link to="/admin/hang-doi-qa">Hàng đợi QA</Link>.</li>
        <li>Gói Miễn phí: tập câu cố định, chọn trong các câu đang phục vụ. Môn có ít hơn {free_limit} câu thì mở toàn bộ.</li>
      </ul>
    </div>
  );
}

interface Queue { key: string; label: string; filter: Record<string, string>; count: number; ready_upstream: number }

export function QaQueues() {
  const r = useAsync(() => get<{ items: Queue[] }>("/api/admin/qa-queues"), []);
  if (r.loading) return <Spinner />;
  if (r.error || !r.data) return <ErrorBox error={r.error} />;
  return (
    <div>
      <PageHead title="Hàng đợi QA" />
      <p className="muted">Mỗi hàng đợi mở danh sách câu hỏi đã lọc sẵn; trong trang chi tiết có bản xem trước đúng như học sinh thấy, lý do loại trừ và nút bật/tắt phục vụ, ghi đè môn.
        Trạng thái biên tập đến từ ngân hàng nguồn (chỉ đọc); sửa nội dung bằng đề xuất sửa gửi về nhóm biên tập.</p>
      <div className="kpi-grid">
        {r.data.items.map((q) => (
          <Link key={q.key} to={"/admin/cau-hoi" + qs(q.filter)} className={"kpi " + (q.count ? "warn" : "ok")}>
            <span className="v">{num(q.count, 0)}</span>
            <span className="k">{q.label}</span>
            {q.ready_upstream > 0 && q.ready_upstream !== q.count && <span className="k"> · {num(q.ready_upstream, 0)} đang READY ở nguồn</span>}
          </Link>
        ))}
      </div>
    </div>
  );
}
