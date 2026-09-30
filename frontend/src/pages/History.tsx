import { useState } from "react";
import { Empty, ErrorBox, Pager, Spinner, useAsync } from "../components/ui";
import { get, qs } from "../lib/api";
import type { SessionSummary } from "../lib/types";
import { SessionRow } from "./Dashboard";

export default function History() {
  const [mode, setMode] = useState("");
  const [status, setStatus] = useState("submitted");
  const [page, setPage] = useState(1);
  const { data, error, loading } = useAsync(
    () => get<{ total: number; items: SessionSummary[] }>(`/api/sessions${qs({ mode, status, page, size: 20 })}`), [mode, status, page]);
  return (
    <div className="container narrow page">
      <h1>Lịch sử làm bài</h1>
      <div className="row mb">
        <div className="seg">
          {[["submitted", "Đã nộp"], ["in_progress", "Đang làm"], ["abandoned", "Đã huỷ"]].map(([k, v]) => (
            <button key={k} className={status === k ? "on" : ""} onClick={() => { setStatus(k); setPage(1); }}>{v}</button>
          ))}
        </div>
        <div className="seg">
          {[["", "Tất cả"], ["exam", "Đề thi"], ["practice", "Luyện tập"]].map(([k, v]) => (
            <button key={k} className={mode === k ? "on" : ""} onClick={() => { setMode(k); setPage(1); }}>{v}</button>
          ))}
        </div>
      </div>
      {loading ? <Spinner /> : error ? <ErrorBox error={error} /> : !data?.items.length ? <Empty title="Chưa có bài làm nào" /> : (
        <>
          <div className="stack">{data.items.map((s) => <SessionRow key={s.id} s={s} />)}</div>
          <Pager page={page} size={20} total={data.total} onPage={setPage} />
        </>
      )}
    </div>
  );
}
