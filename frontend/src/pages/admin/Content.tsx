import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, get, post, put, qs } from "../../lib/api";
import { dateTime, num } from "../../lib/format";
import { Confirm, Empty, ErrorBox, Pager, Spinner, useAsync, useToast } from "../../components/ui";
import { errMsg, Json, PageHead, useUrlState } from "./common";

// ------------------------------------------------------------------------------------------ corrections
interface Correction {
  id: number; question_id: number; external_id: string; field: string; old_value: string | null; new_value: string;
  evidence: string | null; note: string | null; status: string; created_at: string; exported_at: string | null;
}

export function Corrections() {
  const u = useUrlState({ status: "proposed" });
  const page = Number(u.get("page") || 1);
  const list = useAsync(() => get<{ total: number; items: Correction[] }>("/api/admin/corrections" + qs({ status: u.get("status"), page, size: 50 })),
    [u.get("status"), page]);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const doExport = async () => {
    setBusy(true);
    try {
      const text = await api<string>("/api/admin/corrections/export", { method: "POST", raw: true });
      const url = URL.createObjectURL(new Blob([text], { type: "application/x-ndjson" }));
      const a = document.createElement("a");
      a.href = url; a.download = "hsa_app_corrections.jsonl"; a.click();
      URL.revokeObjectURL(url);
      toast(`Đã xuất ${text.trim() ? text.trim().split("\n").length : 0} đề xuất.`, "ok");
      setConfirm(false); list.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  return (
    <div>
      <PageHead title="Đề xuất sửa">
        <button className="btn" onClick={() => setConfirm(true)}>Xuất JSONL cho HSA-question-bank</button>
      </PageHead>
      <div className="alert info mb">Lớp sửa lỗi của ứng dụng, khoá theo mã cq_… bất biến. Tệp xuất theo định dạng đề xuất của HSA-question-bank
        (question_id, field, new_value, evidence, editor, date, note); ngân hàng gốc không bao giờ bị ghi đè từ ứng dụng.</div>
      <div className="tabs">
        {[["proposed", "Đề xuất"], ["exported", "Đã xuất"], ["applied", "Đã áp dụng"], ["rejected", "Bác bỏ"], ["", "Tất cả"]].map(([k, l]) => (
          <button key={k} className={u.get("status") === k ? "active" : ""} onClick={() => u.set({ status: k || "" })}>{l}</button>
        ))}
      </div>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Không có đề xuất." /> : (
        <>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>#</th><th>Câu hỏi</th><th>Trường</th><th>Giá trị mới</th><th>Bằng chứng</th><th>Trạng thái</th><th>Ngày</th></tr></thead>
            <tbody>{list.data.items.map((c) => (
              <tr key={c.id}>
                <td>{c.id}</td>
                <td><Link className="mono" to={`/admin/cau-hoi/${c.question_id}`}>{c.external_id}</Link></td>
                <td className="mono small">{c.field}</td>
                <td className="small" style={{ maxWidth: 360 }}>{c.new_value.slice(0, 300)}</td>
                <td className="small">{c.evidence || "—"}</td>
                <td><span className="badge">{c.status}</span>{c.exported_at && <div className="small muted">{dateTime(c.exported_at)}</div>}</td>
                <td className="small nowrap">{dateTime(c.created_at)}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pager page={page} size={50} total={list.data.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
      {confirm && (
        <Confirm title="Xuất đề xuất sửa" confirmText="Xuất và đánh dấu đã xuất" busy={busy} onConfirm={doExport} onClose={() => setConfirm(false)}>
          Tất cả đề xuất ở trạng thái "Đề xuất" sẽ được xuất ra tệp JSONL và chuyển sang "Đã xuất".
        </Confirm>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ banks & sync
interface Bank { id: number; code: string; name: string; source_kind: string; description: string | null; is_active: boolean; questions: number; served: number }
interface Run {
  id: number; status: string; started_at: string; finished_at: string | null; source_fingerprint: string | null;
  checkpoint: string | null; stats: Record<string, number | string[]>; error: string | null; triggered_by: string | null;
}
const STAT_KEYS = ["seen", "created", "updated", "unchanged", "versions_created", "content_changed", "removed", "restored", "errors", "manifest_states", "seconds"];

export function Banks() {
  const banks = useAsync(() => get<{ items: Bank[] }>("/api/admin/banks"), []);
  const runs = useAsync(() => get<{ items: Run[]; upstream_available: boolean }>("/api/admin/sync-runs"), []);
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"sync" | "policy" | null>(null);
  const [imp, setImp] = useState({ code: "", name: "" });
  const [result, setResult] = useState<unknown>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const running = runs.data?.items.some((r) => r.status === "running");
  const { reload: reloadRuns } = runs;
  const { reload: reloadBanks } = banks;

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => { reloadRuns(); reloadBanks(); }, 5000);
    return () => clearInterval(t);
  }, [running, reloadRuns, reloadBanks]);

  const sync = async () => {
    setBusy(true);
    try {
      const r = await post<{ queued: boolean; already_running: boolean }>("/api/admin/sync" + (full ? "?full=true" : ""));
      toast(r.already_running ? "Đang có phiên đồng bộ chạy; yêu cầu sẽ được thực hiện sau khi xong."
        : "Đã xếp lịch đồng bộ — bộ lập lịch sẽ chạy trong vài phút (ưu tiên thấp).", "ok");
      setConfirm(null);
      setTimeout(runs.reload, 1500);
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const recompute = async () => {
    setBusy(true);
    try {
      const r = await post<{ evaluated: number; changed: number }>("/api/admin/recompute-policy");
      toast(`Đã tính lại: ${r.evaluated} câu, ${r.changed} thay đổi.`, "ok");
      setConfirm(null); banks.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const doImport = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f || !imp.code) return;
    setBusy(true); setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      if (imp.name) fd.append("name", imp.name);
      const r = await api(`/api/admin/banks/${encodeURIComponent(imp.code)}/import`, { method: "POST", body: fd });
      setResult(r); toast("Đã nhập ngân hàng.", "ok"); banks.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };

  return (
    <div className="stack">
      <PageHead title="Ngân hàng câu hỏi & đồng bộ">
        <button className="btn secondary" onClick={() => setConfirm("policy")}>Tính lại chính sách</button>
      </PageHead>
      {banks.loading ? <Spinner /> : banks.error ? <ErrorBox error={banks.error} /> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Mã</th><th>Tên</th><th>Nguồn</th><th>Số câu</th><th>Đang phục vụ</th></tr></thead>
          <tbody>{banks.data?.items.map((b) => (
            <tr key={b.id}><td className="mono">{b.code}</td><td>{b.name}<div className="small muted">{b.description}</div></td>
              <td>{b.source_kind}</td><td>{num(b.questions, 0)}</td><td><Link to={`/admin/cau-hoi?bank=${b.code}&served=yes`}>{num(b.served, 0)}</Link></td></tr>
          ))}</tbody>
        </table></div>
      )}

      <div className="card">
        <div className="row between">
          <h3 style={{ margin: 0 }}>Đồng bộ từ HSA-question-bank</h3>
          <div className="row">
            <label className="check"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Dựng lại toàn bộ</label>
            <button className="btn" disabled={busy || running || runs.data?.upstream_available === false} onClick={() => setConfirm("sync")}>
              {running ? "Đang đồng bộ…" : "Đồng bộ ngay"}
            </button>
          </div>
        </div>
        <p className="small muted mt">Đồng bộ đọc (chỉ đọc) cơ sở dữ liệu chuẩn và lớp biên tập, tăng dần theo mã cq_…, không tạo trùng lặp, không xoá lịch sử bài làm.
          Nội dung thay đổi tạo phiên bản mới; bài thi đã nộp giữ nguyên phiên bản cũ.</p>
        {runs.data?.upstream_available === false && <div className="alert warn">Không tìm thấy dữ liệu nguồn (thư mục upstream chưa được gắn).</div>}
        {runs.loading ? <Spinner /> : runs.error ? <ErrorBox error={runs.error} /> : !runs.data?.items.length ? <Empty title="Chưa có lần đồng bộ nào." /> : (
          <div className="table-wrap mt"><table className="data">
            <thead><tr><th>#</th><th>Trạng thái</th><th>Bắt đầu</th><th>Kết thúc</th>{STAT_KEYS.map((k) => <th key={k}>{k}</th>)}<th>Fingerprint</th><th>Lỗi</th></tr></thead>
            <tbody>{runs.data.items.map((r) => (
              <tr key={r.id}>
                <td>{r.id}<div className="small muted">{r.triggered_by}</div></td>
                <td><span className={"badge " + (r.status === "ok" ? "ok" : r.status === "running" ? "brand" : "bad")}>{r.status}</span></td>
                <td className="small nowrap">{dateTime(r.started_at)}</td>
                <td className="small nowrap">{dateTime(r.finished_at)}</td>
                {STAT_KEYS.map((k) => <td key={k} className="small">{typeof r.stats?.[k] === "number" ? num(r.stats[k] as number, 1) : "—"}</td>)}
                <td className="mono small">{r.source_fingerprint}</td>
                <td className="small" style={{ maxWidth: 260, color: "var(--bad)" }}>{r.error}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      <div className="card">
        <h3>Nhập ngân hàng bổ sung (JSONL)</h3>
        <p className="small muted">Mỗi dòng là một câu hỏi theo định dạng của ứng dụng (xem <span className="mono">docs/QUESTION_IMPORT_FORMAT.md</span>):
          external_id, type, subject, status, stem_md, options[{"{"}label, md{"}"}], answer, solution_md, group… Nhập lại cùng tệp là an toàn (không trùng lặp, chỉ tạo phiên bản khi nội dung đổi).
          Không dùng mã <span className="mono">hsa</span> — ngân hàng HSA chỉ được đồng bộ từ nguồn.</p>
        <div className="toolbar">
          <div className="field"><label htmlFor="b-code">Mã ngân hàng</label>
            <input id="b-code" className="input" value={imp.code} placeholder="vd: de_truong_2025" onChange={(e) => setImp({ ...imp, code: e.target.value.toLowerCase() })} /></div>
          <div className="field"><label htmlFor="b-name">Tên (khi tạo mới)</label>
            <input id="b-name" className="input" value={imp.name} onChange={(e) => setImp({ ...imp, name: e.target.value })} /></div>
          <div className="field"><label htmlFor="b-file">Tệp .jsonl (≤ 50 MB)</label>
            <input id="b-file" ref={fileRef} className="input" type="file" accept=".jsonl,.json,.txt" /></div>
          <button className="btn" disabled={busy || !imp.code} onClick={doImport}>{busy ? "Đang nhập…" : "Nhập"}</button>
        </div>
        {result != null && <Json value={result} />}
      </div>

      {confirm === "sync" && (
        <Confirm title="Chạy đồng bộ" confirmText="Bắt đầu" busy={busy} onConfirm={sync} onClose={() => setConfirm(null)}>
          {full ? "Dựng lại toàn bộ sẽ đọc lại mọi câu hỏi (tốn thời gian và CPU). " : "Đồng bộ tăng dần: chỉ dựng lại câu có thay đổi. "}
          Tiến trình chạy nền với ưu tiên thấp.
        </Confirm>
      )}
      {confirm === "policy" && (
        <Confirm title="Tính lại chính sách phục vụ" busy={busy} onConfirm={recompute} onClose={() => setConfirm(null)}>
          Áp dụng lại chính sách phục vụ hiện hành cho mọi câu hỏi. Tập câu học sinh nhận được có thể thay đổi ngay.
        </Confirm>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------ subjects
interface Subject {
  code: string; name: string; short_name: string | null; color: string | null; sort_order: number; is_active: boolean;
  practice_enabled: boolean; questions: number; served: number; aliases: { bank: string; source_value: string }[];
}
const EMPTY: Subject = { code: "", name: "", short_name: "", color: "#2563eb", sort_order: 100, is_active: true, practice_enabled: true, questions: 0, served: 0, aliases: [] };

function SubjectRow({ s, isNew, onSaved }: { s: Subject; isNew?: boolean; onSaved: () => void }) {
  const [f, setF] = useState(s);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    setBusy(true);
    try {
      await put(`/api/admin/subjects/${encodeURIComponent(f.code)}`, {
        code: f.code, name: f.name, short_name: f.short_name || null, color: f.color || null, sort_order: Number(f.sort_order),
        is_active: f.is_active, practice_enabled: f.practice_enabled,
      });
      toast("Đã lưu môn học.", "ok"); onSaved();
      if (isNew) setF(EMPTY);
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const id = `s-${s.code || "new"}`;
  return (
    <tr>
      <td>{isNew ? <input aria-label="Mã môn" className="input" style={{ width: 110 }} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toLowerCase() })} placeholder="ma_mon" />
        : <span className="mono">{f.code}</span>}</td>
      <td><input aria-label="Tên" id={id} className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></td>
      <td><input aria-label="Tên ngắn" className="input" style={{ width: 100 }} value={f.short_name || ""} onChange={(e) => setF({ ...f, short_name: e.target.value })} /></td>
      <td><input aria-label="Màu" type="color" value={f.color || "#2563eb"} onChange={(e) => setF({ ...f, color: e.target.value })} /></td>
      <td><input aria-label="Thứ tự" className="input" type="number" style={{ width: 80 }} value={f.sort_order} onChange={(e) => setF({ ...f, sort_order: Number(e.target.value) })} /></td>
      <td><input aria-label="Hoạt động" type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} /></td>
      <td><input aria-label="Cho luyện tập" type="checkbox" checked={f.practice_enabled} onChange={(e) => setF({ ...f, practice_enabled: e.target.checked })} /></td>
      <td className="small">{isNew ? "—" : `${num(s.served, 0)} / ${num(s.questions, 0)}`}</td>
      <td className="small">{s.aliases.map((a) => `${a.bank}:${a.source_value || "(null)"}`).join(", ")}</td>
      <td><button className="btn sm" disabled={busy || !f.code || !f.name} onClick={save}>{isNew ? "Thêm" : "Lưu"}</button></td>
    </tr>
  );
}

export function Subjects() {
  const list = useAsync(() => get<{ items: Subject[] }>("/api/admin/subjects"), []);
  const banks = useAsync(() => get<{ items: Bank[] }>("/api/admin/banks"), []);
  const [al, setAl] = useState({ bank: "hsa", source_value: "", subject_code: "" });
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const saveAlias = async () => {
    setBusy(true);
    try {
      await put("/api/admin/subject-aliases", al);
      toast("Đã lưu ánh xạ; câu hỏi hiện có đã được gán lại môn.", "ok");
      list.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  if (list.loading) return <Spinner />;
  if (list.error || !list.data) return <ErrorBox error={list.error} />;
  return (
    <div className="stack">
      <PageHead title="Môn học" />
      <div className="table-wrap"><table className="data">
        <thead><tr><th>Mã</th><th>Tên</th><th>Tên ngắn</th><th>Màu</th><th>Thứ tự</th><th>Hoạt động</th><th>Luyện tập</th><th>Phục vụ / tổng</th><th>Ánh xạ nguồn</th><th /></tr></thead>
        <tbody>
          {list.data.items.map((s) => <SubjectRow key={s.code} s={s} onSaved={list.reload} />)}
          <SubjectRow s={EMPTY} isNew onSaved={list.reload} />
        </tbody>
      </table></div>
      <div className="card">
        <h3>Ánh xạ môn của ngân hàng nguồn</h3>
        <p className="small muted">Gán giá trị "subject" của một ngân hàng (vd. literature_language) vào môn của ứng dụng. Để trống giá trị nguồn = câu chưa phân loại (null).</p>
        <div className="toolbar">
          <div className="field"><label htmlFor="a-bank">Ngân hàng</label>
            <select id="a-bank" className="input" value={al.bank} onChange={(e) => setAl({ ...al, bank: e.target.value })}>
              {(banks.data?.items || [{ code: "hsa", name: "hsa" } as Bank]).map((b) => <option key={b.code} value={b.code}>{b.code}</option>)}
            </select></div>
          <div className="field"><label htmlFor="a-src">Giá trị nguồn</label>
            <input id="a-src" className="input" value={al.source_value} onChange={(e) => setAl({ ...al, source_value: e.target.value })} /></div>
          <div className="field"><label htmlFor="a-sub">Môn ứng dụng</label>
            <select id="a-sub" className="input" value={al.subject_code} onChange={(e) => setAl({ ...al, subject_code: e.target.value })}>
              <option value="">— chọn —</option>
              {list.data.items.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
            </select></div>
          <button className="btn" disabled={busy || !al.subject_code} onClick={saveAlias}>Lưu ánh xạ</button>
        </div>
      </div>
    </div>
  );
}
