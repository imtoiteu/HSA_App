import type { Answer, Block, InputSpec, QuestionView } from "../../../lib/types";

export type DimState = "ok" | "na" | "fail" | "pending" | "stale";
export interface Meta {
  me: { id: number; role: string; role_label: string; perms: string[] };
  subjects: { code: string; name: string }[];
  reviewers: { id: number; name: string; email: string; role: string }[];
  queues: Record<string, string>;
  dimensions: Record<string, string>;
  validators: Record<string, string>;
  ai: { configured: boolean; provider: string | null; actions: { code: string; label: string; needs_source_image: boolean }[] };
  types: string[];
  roles: Record<string, string>;
}

export interface QueueItem {
  rank: number; id: number; external_id: string; subject: string | null; type: string; state: string; served: boolean;
  gate: string | null; queues: string[]; status: string; priority: number; dims: Record<string, DimState>;
  findings_open: number; findings_critical: number; assignee: { id: number; name: string } | null; document: string;
  document_id: string | null; source_page: number | null; source_number: number | null; updated_at: string | null;
  draft: boolean; preview: string;
}

export interface ContentDoc {
  type: string; language?: string; input: InputSpec; group: { key: string; header: Block[]; passage: Block[] } | null;
  stem: Block[]; options: { label: string; content: Block[] }[]; solution: Block[] | null; explanation: Block[] | null;
  parts?: import("./PartsEditor").PartEdit[];
}

export interface VersionInfo {
  id: number; version_no: number; origin: string; content: ContentDoc; answer: Answer | null; content_hash: string;
  created_at: string; note: string | null; created_by: string | null;
}

export interface Detail {
  id: number; external_id: string; type: string; subject: string | null; subject_source: string; subject_override: string | null;
  manual: boolean;
  upstream_subject: string | null; classification_review: string | null; topic: string | null; group_key: string | null;
  editorial_state: string; editorial_states: string[]; state_notes: string[]; review_flags: string[]; content_flags: string[];
  content_flag_labels: Record<string, string>; policy_eligible: boolean; policy_reasons: string[]; served: boolean;
  admin_override: string | null; gate: string | null; ready: boolean; answer_source_type: string | null; used_in_attempts: number;
  provenance: Record<string, any>;
  review: {
    status: string; queues: string[]; priority: number; content_hash: string | null; validator_version: number | null;
    validated_at: string | null; upstream_changed: boolean; draft_version_id: number | null;
    assignee: { id: number; name: string } | null; reviewed_by: string | null; reviewed_at: string | null;
    approved_by: string | null; approved_at: string | null; verification_method: string | null; source_page: number | null; complete: boolean;
  };
  dims: Record<string, { state: DimState; required: boolean; confirmed: string | null; by: string | null; at: string | null; note: string | null; label: string }>;
  working: VersionInfo; current_version_id: number; upstream_version_id: number | null;
  preview: QuestionView & { answer: Answer | null }; published_preview: (QuestionView & { answer: Answer | null }) | null;
  findings: { id: number; code: string; label: string; severity: string; dimension: string; detail: string | null; status: string; reason: string | null; resolved_by: string | null; resolved_at: string | null }[];
  versions: { id: number; version_no: number; origin: string; created_at: string; created_by: string | null; note: string | null; content_hash: string; is_current: boolean; is_draft: boolean; is_upstream: boolean }[];
  history: { id: number; action: string; changes: { field: string; old: unknown; new: unknown }[]; note: string | null; source_ref: Record<string, unknown> | null; actor: string | null; at: string; from_version_id: number | null; to_version_id: number | null }[];
}

export interface SourceInfo {
  available: boolean; status: string; representative_occurrence_id: string | null;
  occurrences: { occurrence_id: string; document_id: string; source_question_number: number | null; section: string | null; extraction_method: string | null; unit: string | null; pages: number[]; solution_pages: number[] }[];
  documents: { document_id: string; path: string | null; file_kind: string | null; extraction_method: string | null; document_role: string | null; blob_sha256: string | null; exam_name: string | null; year: number | null; cache: { document_id: string; status: string; pages: number | null; file_kind: string | null; detail: string | null; blob_sha256: string | null } | null }[];
  provenance: Record<string, any>;
}

export const DIM_ICON: Record<DimState, string> = { ok: "✓", na: "–", fail: "✗", pending: "○", stale: "⚠" };
export const DIM_TONE: Record<DimState, string> = { ok: "ok", na: "", fail: "bad", pending: "warn", stale: "warn" };
export const STATUS_LABEL: Record<string, string> = {
  none: "Chưa duyệt", draft: "Bản nháp", reviewed: "Đã duyệt QA", approved: "Đã phê duyệt", returned: "Bị trả lại",
};
// keyboard shortcuts for QA dimensions (only when no input/editor has focus)
export const DIM_KEYS: Record<string, string> = { a: "answer", f: "formula", v: "visual", s: "subject", c: "content", n: "source" };
