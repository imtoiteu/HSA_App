import { useState } from "react";
import { post } from "../lib/api";
import { REPORT_LABELS } from "../lib/format";
import { Modal, useToast } from "./ui";

export function ReportDialog({ questionRef, sessionId, onClose }: { questionRef: number; sessionId?: string; onClose: () => void }) {
  const [category, setCategory] = useState("wrong_answer");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const toast = useToast();
  const send = async () => {
    setBusy(true);
    setErr("");
    try {
      await post("/api/reports", { question_ref: questionRef, category, message, session_id: sessionId });
      toast("Cảm ơn bạn! Báo lỗi đã được gửi.", "ok");
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Báo lỗi câu hỏi" onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Huỷ</button>
      <button className="btn" onClick={send} disabled={busy}>{busy ? "Đang gửi…" : "Gửi báo lỗi"}</button>
    </>}>
      <div className="stack">
        <div className="chip-group" role="radiogroup" aria-label="Loại lỗi">
          {Object.entries(REPORT_LABELS).map(([k, v]) => (
            <button key={k} type="button" role="radio" aria-checked={category === k} className={"chip" + (category === k ? " on" : "")}
                    onClick={() => setCategory(k)}>{v}</button>
          ))}
        </div>
        <div className="field">
          <label htmlFor="rep-msg">Mô tả (không bắt buộc)</label>
          <textarea id="rep-msg" className="input" maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)}
                    placeholder="Ví dụ: đáp án đúng phải là C vì…" />
        </div>
        {err && <div className="error-text">{err}</div>}
      </div>
    </Modal>
  );
}
