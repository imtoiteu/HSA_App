import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { get, post, put } from "../../lib/api";
import { dateTime, vnd } from "../../lib/format";
import { Empty, ErrorBox, Spinner, useAsync, useToast } from "../../components/ui";
import { errMsg, PageHead } from "./common";

interface Availability {
  ok: boolean; total_questions: number; total_minutes: number | null;
  pools: { section: string; pool?: number; fixed?: string; required?: number; available?: number; ok: boolean }[];
}
interface BP {
  id: number; code: string; name: string; description: string | null; kind: "random" | "fixed"; config: Record<string, unknown>;
  version: number; is_published: boolean; price_vnd: number; sort_order: number; updated_at: string; attempts: number;
  access: "free" | "paid"; promo_price_vnd: number | null; attempts_per_purchase: number; purchases: number;
  effective_price: { paid: boolean; price_vnd: number; list_price_vnd: number; promo: boolean };
  availability?: Availability;
}

export function Blueprints() {
  const list = useAsync(() => get<{ items: BP[] }>("/api/admin/blueprints"), []);
  const nav = useNavigate();
  return (
    <div>
      <PageHead title="Cấu trúc đề thi"><Link to="/admin/de-thi/moi" className="btn">+ Tạo đề mới</Link></PageHead>
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? <Empty title="Chưa có đề nào." /> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Tên</th><th>Mã</th><th>Loại</th><th>Truy cập / giá</th><th>Công khai</th><th>Phiên bản</th><th>Lượt làm</th><th>Đủ câu hỏi</th><th>Cập nhật</th></tr></thead>
          <tbody>{list.data.items.map((b) => (
            <tr key={b.id} className="clickable" onClick={() => nav(`/admin/de-thi/${b.id}`)}>
              <td><strong>{b.name}</strong><div className="small muted">{b.availability?.total_questions} câu · {b.availability?.total_minutes ?? "∞"} phút</div></td>
              <td className="mono small">{b.code}</td>
              <td>{b.kind === "fixed" ? "Cố định" : "Ngẫu nhiên"}</td>
              <td>{b.access === "paid" ? <><span className="badge accent">{vnd(b.effective_price.price_vnd)}{b.attempts_per_purchase > 1 ? ` / ${b.attempts_per_purchase} lượt` : ""}</span>
                {b.effective_price.promo && <div className="small muted"><s>{vnd(b.effective_price.list_price_vnd)}</s> khuyến mãi</div>}
                {!b.price_vnd && <div className="small muted">giá mặc định</div>}
                <div className="small muted">{b.purchases} lượt mua</div></> : <span className="badge ok">Miễn phí</span>}</td>
              <td>{b.is_published ? <span className="badge ok">Có</span> : <span className="badge">Ẩn</span>}</td>
              <td>v{b.version}</td>
              <td>{b.attempts}</td>
              <td>{b.availability?.ok ? <span className="badge ok">Đủ</span> : <span className="badge bad">Thiếu</span>}</td>
              <td className="small nowrap">{dateTime(b.updated_at)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

const TEMPLATE = {
  sections: [{ key: "toan", title: "Toán học", duration_minutes: null, pools: [{ subjects: ["math"], types: ["single_choice"], count: 20 }] }],
  timing: "global", duration_minutes: 30, shuffle_options: true, keep_groups_together: true, require_auto_scoring: true,
  feedback: "end", allow_review: true, scoring: { correct: 1, incorrect: 0, unanswered: 0, scale_to: 10 },
};

type ValResult = { valid: boolean; errors?: { loc: string; msg: string }[] } & Partial<Availability>;

function Help() {
  return (
    <div className="card small">
      <h3>Tham số cấu hình</h3>
      <ul style={{ paddingLeft: 18, margin: 0 }}>
        <li><span className="mono">sections[]</span>: <span className="mono">key</span> (a-z0-9_), <span className="mono">title</span>, <span className="mono">description</span>, <span className="mono">duration_minutes</span> (bắt buộc khi timing=per_section), <span className="mono">points_per_question</span>, <span className="mono">order</span> pool|shuffled.</li>
        <li>Đề ngẫu nhiên: <span className="mono">pools[{"{"}subjects, types, exam_systems, topics, banks, cognitive_levels, count{"}"}]</span> (danh sách rỗng = bất kỳ; <span className="mono">cognitive_levels</span>: NB/TH/VD/VDC nếu ngân hàng có).</li>
        <li>Câu chỉ định: <span className="mono">items[{"{"}external_id, bank{"}"}]</span> theo đúng thứ tự; có thể kết hợp với <span className="mono">pools</span> trong cùng phần (câu chỉ định đứng trước, câu ngẫu nhiên không trùng lặp).</li>
        <li><span className="mono">timing</span>: none | global (cần <span className="mono">duration_minutes</span>) | per_section.</li>
        <li><span className="mono">shuffle_options</span>, <span className="mono">keep_groups_together</span>, <span className="mono">max_group_size</span>, <span className="mono">require_auto_scoring</span> (chỉ câu chấm tự động), <span className="mono">feedback</span> end|immediate, <span className="mono">allow_review</span>.</li>
        <li><span className="mono">scoring</span>: correct, incorrect (vd −0.25), unanswered, multi_choice all_or_nothing|partial, tf_sequence all_or_nothing|thpt2025|per_statement, numeric_tolerance, scale_to (vd 150).</li>
      </ul>
      <p className="muted mt">Đổi cấu hình tạo phiên bản mới; bài đã làm giữ nguyên bản chụp cấu hình cũ. Đổi giá không ảnh hưởng lượt đã mua.</p>
    </div>
  );
}

function Structure({ text }: { text: string }) {
  const parsed = useMemo(() => { try { return JSON.parse(text); } catch { return null; } }, [text]);
  if (!parsed) return <div className="error-text small">JSON chưa hợp lệ.</div>;
  const secs = Array.isArray(parsed.sections) ? parsed.sections : [];
  return (
    <div className="stack small">
      <div>Tính giờ: <strong>{String(parsed.timing ?? "global")}</strong>{parsed.duration_minutes ? ` · ${parsed.duration_minutes} phút` : ""}</div>
      {secs.map((s: any, i: number) => (
        <div key={i} className="card flat" style={{ padding: 10 }}>
          <strong>{s.title}</strong> <span className="mono muted">{s.key}</span>{s.duration_minutes ? ` · ${s.duration_minutes} phút` : ""}
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {(s.pools || []).map((p: any, j: number) => (
              <li key={j}>{p.count} câu · môn: {(p.subjects || []).join(", ") || "bất kỳ"} · dạng: {(p.types || []).join(", ") || "bất kỳ"}
                {p.exam_systems?.length ? ` · kỳ thi: ${p.exam_systems.join(", ")}` : ""}{p.banks?.length ? ` · ngân hàng: ${p.banks.join(", ")}` : ""}</li>
            ))}
            {(s.items || []).length > 0 && <li>{s.items.length} câu cố định</li>}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function BlueprintEditor() {
  const { id } = useParams();
  const isNew = !id;
  const nav = useNavigate();
  const toast = useToast();
  const list = useAsync(() => (isNew ? Promise.resolve({ items: [] as BP[] }) : get<{ items: BP[] }>("/api/admin/blueprints")), [id]);
  const [f, setF] = useState({ code: "", name: "", description: "", kind: "random", access: "free", price_vnd: "0", promo: "",
                               attempts: "1", is_published: false, sort_order: "100" });
  const [cfg, setCfg] = useState(JSON.stringify(TEMPLATE, null, 2));
  const [val, setVal] = useState<ValResult | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const bp = list.data?.items.find((b) => String(b.id) === id);

  useEffect(() => {
    if (bp) {
      setF({ code: bp.code, name: bp.name, description: bp.description || "", kind: bp.kind, access: bp.access, price_vnd: String(bp.price_vnd),
             promo: bp.promo_price_vnd ? String(bp.promo_price_vnd) : "", attempts: String(bp.attempts_per_purchase),
             is_published: bp.is_published, sort_order: String(bp.sort_order) });
      setCfg(JSON.stringify(bp.config, null, 2));
      if (bp.availability) setVal({ valid: true, ...bp.availability });
    }
  }, [bp]);

  const parse = (): Record<string, unknown> | null => {
    try { return JSON.parse(cfg); } catch (e) { setErr("Cấu hình không phải JSON hợp lệ: " + (e as Error).message); return null; }
  };
  const validate = async () => {
    setErr("");
    const c = parse();
    if (!c) return;
    try { setVal(await post<ValResult>("/api/admin/blueprints/validate", { config: c })); } catch (e) { setErr(errMsg(e)); }
  };
  const save = async () => {
    setErr("");
    const c = parse();
    if (!c) return;
    const price = Number(f.price_vnd || 0);
    const promo = f.promo.trim() ? Number(f.promo) : null;
    const attempts = Number(f.attempts || 1);
    if (!Number.isInteger(price) || price < 0) { setErr("Giá phải là số nguyên ≥ 0."); return; }
    if (promo != null && (!Number.isInteger(promo) || promo <= 0)) { setErr("Giá khuyến mãi phải là số nguyên > 0 (để trống nếu không có)."); return; }
    if (!Number.isInteger(attempts) || attempts < 1 || attempts > 100) { setErr("Số lượt mỗi lần mua: 1–100."); return; }
    setBusy(true);
    const body = { code: f.code, name: f.name, description: f.description || null, kind: f.kind, config: c, is_published: f.is_published,
                   access: f.access, price_vnd: f.access === "paid" ? price : 0, promo_price_vnd: f.access === "paid" ? promo : null,
                   attempts_per_purchase: attempts, sort_order: Number(f.sort_order) || 100 };
    try {
      const r = isNew ? await post<BP>("/api/admin/blueprints", body) : await put<BP>(`/api/admin/blueprints/${id}`, body);
      toast("Đã lưu đề thi.", "ok");
      if (r.availability) setVal({ valid: true, ...r.availability });
      if (isNew) nav(`/admin/de-thi/${r.id}`, { replace: true }); else list.reload();
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };

  if (!isNew && list.loading) return <Spinner />;
  if (!isNew && (list.error || !bp)) return <ErrorBox error={list.error || new Error("Không tìm thấy đề.")} />;

  return (
    <div className="stack">
      <div className="small"><Link to="/admin/de-thi">← Danh sách đề</Link></div>
      <PageHead title={isNew ? "Tạo đề thi" : `Sửa đề: ${bp?.name}`}>
        {bp && <span className="badge">v{bp.version} · {bp.attempts} lượt làm</span>}
      </PageHead>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.5fr) minmax(0, 1fr)" }}>
        <div className="stack">
          <div className="card stack">
            <div className="grid cols-2">
              <div className="field"><label htmlFor="bp-code">Mã (a-z0-9_)</label>
                <input id="bp-code" className="input" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></div>
              <div className="field"><label htmlFor="bp-name">Tên đề</label>
                <input id="bp-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div className="field"><label htmlFor="bp-kind">Loại</label>
                <select id="bp-kind" className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
                  <option value="random">Ngẫu nhiên theo cấu trúc</option><option value="fixed">Bộ đề cố định</option>
                </select></div>
              <div className="field"><label htmlFor="bp-access">Truy cập</label>
                <select id="bp-access" className="input" value={f.access} onChange={(e) => setF({ ...f, access: e.target.value })}>
                  <option value="free">Miễn phí</option><option value="paid">Có phí (mua lượt)</option>
                </select></div>
              {f.access === "paid" && <>
                <div className="field"><label htmlFor="bp-price">Giá một lần mua (VND, 0 = giá mặc định)</label>
                  <input id="bp-price" className="input" type="number" min={0} step={1000} value={f.price_vnd} onChange={(e) => setF({ ...f, price_vnd: e.target.value })} />
                  <span className="hint">{Number(f.price_vnd) > 0 ? vnd(Number(f.price_vnd)) : <>Dùng giá mặc định (<Link to="/admin/cai-dat?tab=mock_exams">Cài đặt → Đề thi thử</Link>)</>}</span></div>
                <div className="field"><label htmlFor="bp-promo">Giá khuyến mãi (VND, trống = không)</label>
                  <input id="bp-promo" className="input" type="number" min={0} step={1000} value={f.promo} onChange={(e) => setF({ ...f, promo: e.target.value })} /></div>
                <div className="field"><label htmlFor="bp-att">Số lượt làm bài mỗi lần mua</label>
                  <input id="bp-att" className="input" type="number" min={1} max={100} value={f.attempts} onChange={(e) => setF({ ...f, attempts: e.target.value })} /></div>
              </>}
              <div className="field"><label htmlFor="bp-sort">Thứ tự hiển thị</label>
                <input id="bp-sort" className="input" type="number" value={f.sort_order} onChange={(e) => setF({ ...f, sort_order: e.target.value })} /></div>
              <div className="field"><span className="label">Hiển thị</span>
                <label className="check"><input type="checkbox" checked={f.is_published} onChange={(e) => setF({ ...f, is_published: e.target.checked })} /> Công khai cho học sinh</label></div>
            </div>
            <div className="field"><label htmlFor="bp-desc">Mô tả</label>
              <textarea id="bp-desc" className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
            <div className="field"><label htmlFor="bp-cfg">Cấu hình (JSON)</label>
              <textarea id="bp-cfg" className="input code-area" spellCheck={false} value={cfg} onChange={(e) => setCfg(e.target.value)} /></div>
            {err && <div className="alert error">{err}</div>}
            <div className="row">
              <button className="btn secondary" onClick={validate}>Kiểm tra</button>
              <button className="btn" disabled={busy || !f.code || !f.name} onClick={save}>{busy ? "Đang lưu…" : "Lưu đề"}</button>
            </div>
          </div>
          {val && (
            <div className="card">
              <h3>Kết quả kiểm tra</h3>
              {!val.valid ? (
                <ul className="error-text">{val.errors?.map((e, i) => <li key={i}><span className="mono">{e.loc}</span>: {e.msg}</li>)}</ul>
              ) : (
                <>
                  <div className={"alert " + (val.ok ? "ok" : "warn")}>{val.ok ? "Đủ câu hỏi cho mọi phần." : "Một số phần thiếu câu hỏi đang phục vụ."}
                    {" "}Tổng {val.total_questions} câu{val.total_minutes ? ` · ${val.total_minutes} phút` : ""}.</div>
                  <div className="table-wrap mt"><table className="data">
                    <thead><tr><th>Phần</th><th>Nguồn</th><th>Cần</th><th>Có sẵn</th><th /></tr></thead>
                    <tbody>{val.pools?.map((p, i) => (
                      <tr key={i}><td className="mono">{p.section}</td><td>{p.fixed ? <span className="mono">{p.fixed}</span> : `pool #${p.pool}`}</td>
                        <td>{p.required ?? 1}</td><td>{p.available ?? (p.ok ? "✓" : "✗")}</td>
                        <td>{p.ok ? <span className="badge ok">OK</span> : <span className="badge bad">Thiếu</span>}</td></tr>
                    ))}</tbody>
                  </table></div>
                </>
              )}
            </div>
          )}
        </div>
        <div className="stack">
          <div className="card"><h3>Cấu trúc</h3><Structure text={cfg} /></div>
          <Help />
        </div>
      </div>
    </div>
  );
}
