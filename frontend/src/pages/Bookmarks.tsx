import { useState } from "react";
import { Link } from "react-router-dom";
import { AnswerInput, Passage, Solution } from "../components/QuestionBody";
import { RichContent } from "../components/RichContent";
import { Empty, ErrorBox, Pager, Spinner, useAsync, useToast } from "../components/ui";
import { del, get, qs } from "../lib/api";
import type { QuestionView } from "../lib/types";

interface BM { question_ref: number; subject: string | null; note: string | null; created_at: string; revealed: boolean; question: QuestionView | null }

export default function Bookmarks() {
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const toast = useToast();
  const { data, error, loading, reload } = useAsync(() => get<{ total: number; items: BM[] }>(`/api/bookmarks${qs({ page, size: 10 })}`), [page]);
  const remove = async (id: number) => {
    await del(`/api/bookmarks/${id}`);
    toast("Đã bỏ lưu.", "ok");
    reload();
  };
  return (
    <div className="container narrow page">
      <div className="row between">
        <h1>Câu hỏi đã lưu</h1>
        {!!data?.total && <Link to="/luyen-tap?nguon=bookmarks" className="btn">Luyện lại các câu này</Link>}
      </div>
      {loading ? <Spinner /> : error ? <ErrorBox error={error} /> : !data?.items.length ? (
        <Empty icon="🔖" title="Bạn chưa lưu câu hỏi nào">Bấm biểu tượng lưu khi làm bài hoặc xem lại kết quả.</Empty>
      ) : (
        <>
          <div className="stack">
            {data.items.map((b) => (
              <div key={b.question_ref} className="card">
                {b.question ? (
                  <>
                    {b.question.group && open === b.question_ref && <Passage group={b.question.group} lang={b.question.language} />}
                    <RichContent blocks={b.question.stem} />
                    {open === b.question_ref && (
                      <>
                        <AnswerInput input={b.question.input} options={b.question.options} response={null} disabled onChange={() => {}}
                                     reveal={b.revealed ? { answer: b.question.answer } : undefined} />
                        {b.revealed ? <Solution q={b.question} answer={b.question.answer} answerDisplay={b.question.answer_display} />
                          : <div className="alert info mt">Đáp án sẽ hiển thị sau khi bạn nộp bài có câu hỏi này.</div>}
                      </>
                    )}
                  </>
                ) : <div className="muted">Câu hỏi không còn khả dụng.</div>}
                {b.note && <div className="alert info mt small">Ghi chú: {b.note}</div>}
                <div className="row mt">
                  <button className="btn secondary sm" onClick={() => setOpen(open === b.question_ref ? null : b.question_ref)}>
                    {open === b.question_ref ? "Thu gọn" : "Xem đầy đủ"}
                  </button>
                  <button className="btn ghost sm" onClick={() => remove(b.question_ref)}>Bỏ lưu</button>
                </div>
              </div>
            ))}
          </div>
          <Pager page={page} size={10} total={data.total} onPage={setPage} />
        </>
      )}
    </div>
  );
}
