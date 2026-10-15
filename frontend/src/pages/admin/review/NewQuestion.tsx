// "Thêm câu hỏi": creates an empty question in a manual bank and opens it in the Review Studio, where it is
// written with the same editors, saved as drafts, previewed and taken through QA like any other question.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Modal, useToast } from "../../../components/ui";
import { post } from "../../../lib/api";
import { TYPE_LABELS } from "../../../lib/format";
import { errMsg } from "../common";
import { useMeta } from "./useMeta";

const TYPE_HINTS: Record<string, string> = {
  single_choice: "4 phương án A–D (thêm/bớt/sắp xếp được), chọn một đáp án đúng.",
  multiple_choice: "Nhiều phương án, có thể nhiều đáp án đúng.",
  true_false: "Một mệnh đề, đáp án Đúng hoặc Sai.",
  true_false_statements: "Các ý a), b), c)… trong đề, đáp án Đúng/Sai cho từng ý.",
  numeric_response: "Học sinh nhập một số (đáp án số, có thể chấm tự động).",
  short_response: "Trả lời ngắn: đáp án chữ hoặc biểu thức/công thức toán (chấm theo tương đương đại số).",
  error_identification: "Tìm lỗi sai: các phần gạch chân là phương án.",
  constructed_response: "Tự luận: học sinh tự đối chiếu với lời giải.",
  multi_part: "Nhiều ý nhỏ, mỗi ý có dạng trả lời và đáp án riêng.",
};

export function NewQuestionButton({ bank, className = "btn", label = "+ Thêm câu hỏi" }: { bank?: string; className?: string; label?: string }) {
  const meta = useMeta();
  const [open, setOpen] = useState(false);
  if (!meta || !meta.me.perms.includes("edit")) return null;
  return <>
    <button className={className} onClick={() => setOpen(true)}>{label}</button>
    {open && <NewQuestionDialog bank={bank} onClose={() => setOpen(false)} />}
  </>;
}

export function NewQuestionDialog({ bank, onClose }: { bank?: string; onClose: () => void }) {
  const meta = useMeta();
  const nav = useNavigate();
  const toast = useToast();
  const [type, setType] = useState("single_choice");
  const [subject, setSubject] = useState("");
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const types = (meta?.types || []).filter((t) => t !== "open_or_unknown");
  const create = async () => {
    setBusy(true);
    try {
      const r = await post<{ id: number; external_id: string }>("/api/review/questions",
        { type, subject: subject || null, topic: topic.trim() || null, bank: bank || null });
      toast(`Đã tạo câu hỏi ${r.external_id} (bản nháp, chưa phục vụ).`, "ok");
      nav(`/admin/review/q/${r.id}`);
    } catch (e) {
      toast(errMsg(e), "error");
      setBusy(false);
    }
  };
  return (
    <Modal title="Thêm câu hỏi mới" onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Huỷ</button>
      <button className="btn" disabled={busy || !meta} onClick={create}>Tạo và mở Review Studio</button>
    </>}>
      <p className="small muted">Câu hỏi được tạo trong ngân hàng <span className="mono">{bank || "thu_cong"}</span> (nhập thủ công, không cần tài
        liệu nguồn) với mã riêng <span className="mono">manual-…</span>, rồi mở trong Review Studio để soạn đề, phương án, đáp án, lời giải
        (công thức, hình, bảng). Câu mới là bản nháp: chỉ được phục vụ học sinh sau khi qua QA và được phê duyệt.</p>
      <div className="field">
        <label htmlFor="nq-type">Dạng câu</label>
        <select id="nq-type" className="input" value={type} onChange={(e) => setType(e.target.value)}>
          {types.map((t) => <option key={t} value={t}>{TYPE_LABELS[t] || t}</option>)}
        </select>
        {TYPE_HINTS[type] && <div className="small muted">{TYPE_HINTS[type]}</div>}
      </div>
      <div className="grid cols-2 mt">
        <div className="field">
          <label htmlFor="nq-subject">Môn</label>
          <select id="nq-subject" className="input" value={subject} onChange={(e) => setSubject(e.target.value)}>
            <option value="">(chọn sau)</option>
            {(meta?.subjects || []).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="nq-topic">Chủ đề</label>
          <input id="nq-topic" className="input" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="vd: Hàm số mũ" />
        </div>
      </div>
    </Modal>
  );
}
