export const vnd = (n: number | null | undefined) =>
  n == null ? "—" : new Intl.NumberFormat("vi-VN").format(n) + " đ";

export const num = (n: number | null | undefined, digits = 2) =>
  n == null ? "—" : new Intl.NumberFormat("vi-VN", { maximumFractionDigits: digits }).format(n);

export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

export function dateTime(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" });
}

export function dateOnly(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function clock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const p = (x: number) => String(x).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(ss)}` : `${p(m)}:${p(ss)}`;
}

export function duration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} phút`;
  return `${Math.floor(m / 60)} giờ ${m % 60} phút`;
}

export const TYPE_LABELS: Record<string, string> = {
  single_choice: "Trắc nghiệm 1 đáp án",
  multiple_choice: "Trắc nghiệm nhiều đáp án",
  true_false: "Đúng/Sai",
  true_false_statements: "Đúng/Sai theo ý",
  numeric_response: "Điền số",
  short_response: "Trả lời ngắn",
  error_identification: "Tìm lỗi sai",
  constructed_response: "Tự luận",
  open_or_unknown: "Chưa xác định",
};

export const STATE_LABELS: Record<string, string> = {
  READY_TO_SERVE: "Sẵn sàng",
  NEEDS_REVIEW: "Cần xem lại",
  NEEDS_FORMULA_REVIEW: "Cần kiểm tra công thức",
  NEEDS_VISUAL_REVIEW: "Cần kiểm tra hình",
  NEEDS_ANSWER_LINKING: "Thiếu đáp án",
  REJECTED: "Loại",
};

export const REPORT_LABELS: Record<string, string> = {
  wrong_answer: "Đáp án sai",
  broken_formula: "Lỗi công thức",
  broken_image: "Lỗi hình ảnh",
  typo: "Lỗi chính tả",
  unclear: "Đề không rõ",
  other: "Khác",
};

export const ORDER_STATUS: Record<string, [string, string]> = {
  pending: ["Chờ thanh toán", "warn"],
  paid: ["Đã thanh toán", "ok"],
  expired: ["Hết hạn", ""],
  cancelled: ["Đã huỷ", ""],
  refunded: ["Đã hoàn tiền", "bad"],
};
