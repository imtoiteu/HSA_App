import { useEffect, useState } from "react";
import { get, put } from "../../lib/api";
import { STATE_LABELS, TYPE_LABELS } from "../../lib/format";
import { Confirm, ErrorBox, Spinner, useAsync, useToast } from "../../components/ui";
import { copyText, errMsg, PageHead } from "./common";

type Key = "serving_policy" | "payment" | "practice" | "site";
interface SettingResp {
  key: Key; value: Record<string, any>;
  reference?: { states: string[]; reasons: Record<string, string> };
  secrets_configured?: Record<string, boolean>;
  webhook_urls?: Record<string, string>;
}
const TABS: [Key, string][] = [["serving_policy", "Chính sách phục vụ"], ["payment", "Thanh toán"], ["practice", "Luyện tập"], ["site", "Trang web"]];

function toggle(list: string[], v: string): string[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return <label className="check"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> {label}</label>;
}

function Text({ id, label, value, onChange, type = "text", hint }: { id: string; label: string; value: any; onChange: (v: string) => void; type?: string; hint?: string }) {
  return (
    <div className="field"><label htmlFor={id}>{label}</label>
      <input id={id} className="input" type={type} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
      {hint && <span className="hint">{hint}</span>}</div>
  );
}

export default function Settings() {
  const [tab, setTab] = useState<Key>("serving_policy");
  const r = useAsync(() => get<SettingResp>(`/api/admin/settings/${tab}`), [tab]);
  const [v, setV] = useState<Record<string, any> | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const toast = useToast();
  useEffect(() => { setV(r.data ? structuredClone(r.data.value) : null); setMsg(""); }, [r.data]);

  const save = async () => {
    if (!v) return;
    setBusy(true); setMsg("");
    try {
      const res = await put<{ value: Record<string, any>; recomputed?: { evaluated: number; changed: number } }>(`/api/admin/settings/${tab}`, { value: v });
      toast("Đã lưu cài đặt.", "ok");
      if (res.recomputed) setMsg(`Đã áp dụng lại chính sách cho ${res.recomputed.evaluated} câu hỏi; ${res.recomputed.changed} câu thay đổi trạng thái phục vụ.`);
      setConfirm(false); r.reload();
    } catch (e) { toast(errMsg(e), "error"); setConfirm(false); } finally { setBusy(false); }
  };
  const set = (patch: Record<string, any>) => setV((x) => ({ ...(x || {}), ...patch }));

  let body = null;
  if (v && r.data) {
    if (tab === "serving_policy") {
      const reasons = r.data.reference?.reasons || {};
      body = (
        <div className="stack">
          <div className="alert warn">Thay đổi chính sách áp dụng ngay cho toàn bộ câu hỏi: tập câu học sinh nhận được trong luyện tập và đề thi mới sẽ thay đổi. Bài đã làm không bị ảnh hưởng.</div>
          <div className="card flat"><h3>Trạng thái biên tập được phục vụ</h3>
            <div className="stack">{(r.data.reference?.states || []).map((s) => (
              <Check key={s} label={`${STATE_LABELS[s] || s} (${s})`} checked={(v.allowed_states || []).includes(s)} onChange={() => set({ allowed_states: toggle(v.allowed_states || [], s) })} />
            ))}</div></div>
          <div className="card flat"><h3>Dạng câu hỏi được phục vụ</h3>
            <div className="grid cols-2">{Object.entries(TYPE_LABELS).map(([k, l]) => (
              <Check key={k} label={l} checked={(v.allowed_types || []).includes(k)} onChange={() => set({ allowed_types: toggle(v.allowed_types || [], k) })} />
            ))}</div></div>
          <div className="card flat"><h3>Kiểm tra nội dung chặn phục vụ</h3>
            <div className="stack">{Object.entries(reasons).map(([k, l]) => (
              <Check key={k} label={`${l} (${k})`} checked={(v.blocking_reasons || []).includes(k)} onChange={() => set({ blocking_reasons: toggle(v.blocking_reasons || [], k) })} />
            ))}</div></div>
          <div className="card flat stack"><h3>Chấm điểm</h3>
            <Check label="Luyện tập được dùng câu tự đối chiếu đáp án (không chấm tự động)" checked={!!v.practice_allow_self_check} onChange={(b) => set({ practice_allow_self_check: b })} />
            <Check label="Chấm tự động đáp án dạng chữ (so khớp chính xác)" checked={!!v.text_answers_auto_scored} onChange={(b) => set({ text_answers_auto_scored: b })} />
            <div className="row">
              <Text id="sp-tol" label="Sai số tương đối cho đáp án số" type="number" value={v.numeric_tolerance} onChange={(s) => set({ numeric_tolerance: Number(s) })} />
              <Text id="sp-cov" label="Tỉ lệ phủ chủ đề tối thiểu (0–1) để mở luyện theo chủ đề" type="number" value={v.topic_min_coverage} onChange={(s) => set({ topic_min_coverage: Number(s) })} />
            </div>
          </div>
        </div>
      );
    } else if (tab === "payment") {
      const sec = r.data.secrets_configured || {};
      body = (
        <div className="stack">
          <Check label="Bật thanh toán" checked={!!v.enabled} onChange={(b) => set({ enabled: b })} />
          <div className="grid cols-2">
            <Text id="pm-ttl" label="Thời hạn đơn (phút)" type="number" value={v.order_ttl_minutes} onChange={(s) => set({ order_ttl_minutes: Number(s) })} />
            <Text id="pm-pre" label="Tiền tố mã đơn" value={v.code_prefix} onChange={(s) => set({ code_prefix: s.toUpperCase() })} hint="Chữ in hoa/số, xuất hiện trong nội dung chuyển khoản." />
            <Text id="pm-bin" label="Mã BIN ngân hàng (NAPAS, 6 số)" value={v.bank_bin} onChange={(s) => set({ bank_bin: s.trim() })} hint="vd: 970436 Vietcombank, 970422 MB, 970407 Techcombank" />
            <Text id="pm-bank" label="Tên ngân hàng" value={v.bank_name} onChange={(s) => set({ bank_name: s })} />
            <Text id="pm-acc" label="Số tài khoản nhận" value={v.account_number} onChange={(s) => set({ account_number: s.trim() })} />
            <Text id="pm-name" label="Tên chủ tài khoản" value={v.account_name} onChange={(s) => set({ account_name: s.toUpperCase() })} />
          </div>
          <div className="card flat"><h3>Kênh xác nhận thanh toán</h3>
            <div className="stack">
              {[["manual", "Đối soát thủ công (admin xác nhận)"], ["sepay", "SePay webhook"], ["casso", "Casso webhook"], ["generic", "Webhook tổng quát (HMAC)"]].map(([k, l]) => (
                <div key={k} className="row">
                  <Check label={l} checked={(v.providers || []).includes(k)} onChange={() => set({ providers: toggle(v.providers || [], k) })} />
                  {k !== "manual" && (sec[k] ? <span className="badge ok">Đã cấu hình khoá bí mật</span> : <span className="badge bad">Chưa có khoá bí mật</span>)}
                </div>
              ))}
            </div>
          </div>
          <div className="card flat">
            <h3>Địa chỉ webhook</h3>
            <div className="stack">{Object.entries(r.data.webhook_urls || {}).map(([k, url]) => (
              <div key={k} className="row"><span className="badge" style={{ width: 70 }}>{k}</span>
                <input className="input mono grow" readOnly value={url} aria-label={`Webhook ${k}`} style={{ flex: 1 }} />
                <button className="btn secondary sm" onClick={() => { copyText(url); toast("Đã sao chép.", "ok"); }}>Sao chép</button></div>
            ))}</div>
            <ul className="small muted mt" style={{ paddingLeft: 18 }}>
              <li>Khoá bí mật chỉ đặt qua biến môi trường của máy chủ (HSA_SEPAY_API_KEY, HSA_CASSO_SECURE_TOKEN, HSA_GENERIC_WEBHOOK_SECRET), không bao giờ lưu trong cơ sở dữ liệu hay giao diện.</li>
              <li>SePay: tạo webhook trỏ tới URL sepay, kiểu xác thực "API Key" với cùng giá trị HSA_SEPAY_API_KEY; chỉ nhận giao dịch tiền vào.</li>
              <li>Casso: khai báo webhook với URL casso và "Secure Token" trùng HSA_CASSO_SECURE_TOKEN.</li>
              <li>Webhook tổng quát: gửi JSON {"{"}txn_id, amount, content{"}"} với header X-Signature = HMAC-SHA256(body, HSA_GENERIC_WEBHOOK_SECRET).</li>
              <li>Hệ thống khớp đơn theo mã đơn trong nội dung chuyển khoản; mỗi giao dịch chỉ được ghi nhận một lần. Giao dịch không khớp nằm ở mục Giao dịch để đối soát.</li>
            </ul>
          </div>
        </div>
      );
    } else if (tab === "practice") {
      body = (
        <div className="stack">
          <Check label="Luyện tập miễn phí" checked={!!v.free} onChange={(b) => set({ free: b })} />
          <div className="row">
            <Text id="pr-max" label="Số câu tối đa mỗi lượt" type="number" value={v.max_questions} onChange={(s) => set({ max_questions: Number(s) })} />
            <Text id="pr-def" label="Số câu mặc định" type="number" value={v.default_questions} onChange={(s) => set({ default_questions: Number(s) })} />
          </div>
        </div>
      );
    } else {
      body = (
        <div className="stack">
          <Text id="st-name" label="Tên trang" value={v.name} onChange={(s) => set({ name: s })} />
          <Text id="st-sup" label="Liên hệ hỗ trợ" value={v.support_contact} onChange={(s) => set({ support_contact: s })} />
          <div className="field"><label htmlFor="st-ann">Thông báo hiển thị cho học sinh</label>
            <textarea id="st-ann" className="input" value={v.announcement || ""} onChange={(e) => set({ announcement: e.target.value })} /></div>
        </div>
      );
    }
  }

  return (
    <div>
      <PageHead title="Cài đặt" />
      <div className="tabs" role="tablist">
        {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {r.loading ? <Spinner /> : r.error ? <ErrorBox error={r.error} /> : (
        <div className="card stack">
          {body}
          {msg && <div className="alert ok">{msg}</div>}
          <div className="row">
            <button className="btn" disabled={busy} onClick={() => (tab === "serving_policy" ? setConfirm(true) : save())}>{busy ? "Đang lưu…" : "Lưu cài đặt"}</button>
            <button className="btn ghost" onClick={() => r.data && setV(structuredClone(r.data.value))}>Hoàn tác</button>
          </div>
        </div>
      )}
      {confirm && (
        <Confirm title="Áp dụng chính sách phục vụ mới?" busy={busy} onConfirm={save} onClose={() => setConfirm(false)} confirmText="Lưu và áp dụng">
          Toàn bộ câu hỏi sẽ được đánh giá lại ngay. Học sinh có thể nhận thêm hoặc bớt câu hỏi.
        </Confirm>
      )}
    </div>
  );
}
