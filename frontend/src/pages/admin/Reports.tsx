import { useState } from "react";
import { Link } from "react-router-dom";
import { get, patch, qs } from "../../lib/api";
import { dateTime, REPORT_LABELS } from "../../lib/format";
import { Empty, ErrorBox, Modal, Pager, Spinner, useAsync, useToast } from "../../components/ui";
import { errMsg, PageHead, useUrlState } from "./common";

interface Report {
  id: number; question_id: number; external_id: string; category: string; message: string | null; status: string;
  admin_note: string | null; created_at: string; session_id: string | null; question_version_id: number | null;
}
const STATUSES: [string, string][] = [["open", "Mới"], ["triaged", "Đang xử lý"], ["resolved", "Đã xử lý"], ["rejected", "Bác bỏ"]];
const SIZE = 50;

export default function Reports() {
  const u = useUrlState({ status: "open" });
  const status = u.get("status");
  const page = Number(u.get("page") || 1);
  const list = useAsync(() => get<{ total: number; items: Report[] }>("/api/admin/reports" + qs({ status, category: u.get("category"), page, size: SIZE })),
    [status, u.get("category"), page]);
  const [edit, setEdit] = useState<{ r: Report; status: string; note: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const save = async () => {
    if (!edit) return;
    setBusy(true);
    try {
      await patch(`/api/admin/reports/${edit.r.id}`, { status: edit.status, admin_note: edit.note || null });
      toast("Đã cập nhật báo lỗi.", "ok");
      setEdit(null); list.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHead title="Báo lỗi từ học sinh" />
      <div className="tabs" role="tablist">
        {STATUSES.map(([k, l]) => <button key={k} role="tab" aria-selected={status === k} className={status === k ? "active" : ""} onClick={() => u.set({ status: k })}>{l}</button>)}
      </div>
      <div className="toolbar">
        <div className="field"><label htmlFor="r-cat">Loại lỗi</label>
          <select id="r-cat" className="input" value={u.get("category")} onChange={(e) => u.set({ category: e.target.value })}>
            <option value="">Tất cả</option>
            {Object.entries(REPORT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select></div>
      </div>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Không có báo lỗi." icon="✅" /> : (
        <>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>#</th><th>Loại</th><th>Câu hỏi</th><th>Nội dung</th><th>Ngày</th><th /></tr></thead>
            <tbody>{list.data.items.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td><span className="badge warn">{REPORT_LABELS[r.category] || r.category}</span></td>
                <td><Link className="mono" to={`/admin/cau-hoi/${r.question_id}`}>{r.external_id}</Link>
                  {r.question_version_id && <div className="small muted">phiên bản #{r.question_version_id}</div>}</td>
                <td className="small" style={{ maxWidth: 380 }}>{r.message || <span className="muted">(không mô tả)</span>}
                  {r.admin_note && <div className="muted">Ghi chú: {r.admin_note}</div>}</td>
                <td className="small nowrap">{dateTime(r.created_at)}</td>
                <td><button className="btn secondary sm" onClick={() => setEdit({ r, status: r.status === "open" ? "triaged" : r.status, note: r.admin_note || "" })}>Xử lý</button></td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pager page={page} size={SIZE} total={list.data.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
      {edit && (
        <Modal title={`Báo lỗi #${edit.r.id}`} onClose={() => setEdit(null)} actions={<>
          <button className="btn secondary" onClick={() => setEdit(null)}>Huỷ</button>
          <button className="btn" disabled={busy} onClick={save}>Lưu</button>
        </>}>
          <div className="stack">
            <div className="field"><label htmlFor="e-st">Trạng thái</label>
              <select id="e-st" className="input" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}>
                {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select></div>
            <div className="field"><label htmlFor="e-note">Ghi chú quản trị</label>
              <textarea id="e-note" className="input" value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })} /></div>
            <div className="small muted">Để sửa nội dung, mở câu hỏi và ghi "Đề xuất sửa" (liên kết với báo lỗi này).</div>
          </div>
        </Modal>
      )}
    </div>
  );
}
