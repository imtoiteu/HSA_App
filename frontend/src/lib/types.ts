// Render-ready content model produced by the backend (backend/app/content/render.py).
export type Mark = "b" | "i" | "u" | "x" | "sup" | "sub";
export type Inline =
  | { t: "s"; v: string; m?: Mark[] }
  | { t: "m"; tex: string; review?: boolean }
  | { t: "br" }
  | { t: "tab" }
  | { t: "range" }
  | { t: "warn"; v: string }
  | { t: "img"; src: string; w?: number; h?: number; inline?: boolean; review?: boolean };
export type Cell = { c: Block[]; colspan?: number; rowspan?: number };
export type Block =
  | { t: "p"; c: Inline[]; display?: boolean }
  | { t: "img"; src: string; w?: number; h?: number }
  | { t: "table"; rows: Cell[][] }
  | { t: "warn"; v: string };

export type InputSpec =
  | { kind: "choice"; multi: boolean }
  | { kind: "numeric" }
  | { kind: "text" }
  | { kind: "tf_sequence"; n: number }
  | { kind: "boolean" }
  | { kind: "none" };

export type Answer =
  | { kind: "choice"; labels: string[] }
  | { kind: "numeric"; value: number; text: string; unit?: string | null }
  | { kind: "text"; text: string; accepted?: string[] }
  | { kind: "tf_sequence"; values: boolean[] }
  | { kind: "boolean"; value: boolean };

export type Response = { labels?: string[]; value?: string | boolean | null; text?: string; values?: (boolean | null)[] } | null;

export interface Group { key: string; header: Block[]; passage: Block[] }
export interface OptionView { key: string; display: string; content: Block[] }
export interface QuestionView {
  type: string;
  language?: string;
  input: InputSpec;
  group?: Group | null;
  stem: Block[];
  options: OptionView[];
  answer?: Answer | null;
  answer_display?: string[];
  solution?: Block[] | null;
  explanation?: Block[] | null;
}

export interface SessionItem {
  position: number;
  section_index: number;
  question: QuestionView;
  response: Response;
  flagged: boolean;
  checked: boolean;
  scoring_mode: "auto" | "self_check" | "none";
  self_assessment: "correct" | "incorrect" | null;
  question_ref: number;
  points: number;
  bookmarked?: boolean;
  answer?: Answer | null;
  answer_display?: string[];
  solution?: Block[] | null;
  explanation?: Block[] | null;
  outcome?: "correct" | "incorrect" | "partial" | "unanswered" | "ungraded" | null;
  points_awarded?: number | null;
}

export interface SectionInfo {
  index: number;
  key: string;
  title: string;
  description?: string | null;
  duration_minutes?: number | null;
  count: number;
  state: "active" | "locked" | "done";
}

export interface Counts { correct: number; incorrect: number; partial: number; unanswered: number; ungraded: number }

export interface SessionData {
  id: string;
  title: string;
  mode: "exam" | "practice";
  status: "in_progress" | "submitted" | "abandoned";
  feedback: "end" | "immediate";
  timing: "none" | "global" | "per_section";
  server_now: string;
  started_at: string;
  deadline_at: string | null;
  current_section: number;
  sections: SectionInfo[];
  total: number;
  submitted_at: string | null;
  submit_reason: string | null;
  score: number | null;
  max_score: number | null;
  result: {
    counts: Counts;
    sections: { key: string; title: string; score: number; max_score: number; total: number; counts: Counts }[];
    scaled: number | null;
    scale_to: number | null;
    total: number;
    self_assessed?: { correct: number; incorrect: number };
    duration_seconds?: number;
  } | null;
  allow_review: boolean;
  blueprint_id: number | null;
  seed: string;
  items: SessionItem[];
}

export interface SessionSummary {
  id: string;
  title: string;
  mode: "exam" | "practice";
  status: string;
  created_at: string | null;
  started_at: string;
  submitted_at: string | null;
  deadline_at: string | null;
  total: number;
  answered: number;
  score: number | null;
  max_score: number | null;
  scaled: number | null;
  counts: Counts | null;
  blueprint_id: number | null;
}

export interface User { id: number; email: string; display_name: string; role: "student" | "admin"; csrf_token?: string | null }

export interface Blueprint {
  id: number;
  code: string;
  name: string;
  description: string | null;
  kind: string;
  price_vnd: number;
  total_questions: number;
  total_minutes: number | null;
  timing: string;
  sections: { key: string; title: string; description?: string | null; duration_minutes?: number | null; count: number }[];
  access: { free: boolean; allowed: boolean; price_vnd?: number; remaining_attempts?: number | null; unlimited?: boolean; admin?: boolean };
}

export interface SubjectInfo { code: string; name: string; short_name: string | null; color: string | null; available: number }

export interface Catalog {
  subjects: SubjectInfo[];
  blueprints: Blueprint[];
  served_total: number;
  topics_enabled: boolean;
  topics: { subject: string; topic: string; available: number }[];
  practice: { max_questions: number; default_questions: number };
  site: { name: string; announcement: string; support_contact: string };
}

export interface Order {
  code: string;
  status: "pending" | "paid" | "expired" | "cancelled" | "refunded";
  amount_vnd: number;
  name: string;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  transfer_content: string;
  blueprint_id: number | null;
  product_id: number | null;
  bank: { bank_name: string; account_number: string; account_name: string; bin: string } | null;
  qr_svg: string | null;
  qr_payload: string | null;
}
