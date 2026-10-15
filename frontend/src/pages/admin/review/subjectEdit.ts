// Subject/topic of the question being edited in Review Studio: part of the edit state, so a changed "Môn" dropdown
// counts as unsaved and is written by Save / Save & Next / Approve (not only shown in the form).

export type SubjectFields = { subject: string; topic: string };

/** The dropdown's value: the admin override, else the effective subject ("" = follow the source). */
export function subjectFields(d: { subject: string | null; subject_override: string | null; topic: string | null }): SubjectFields {
  return { subject: d.subject_override || d.subject || "", topic: d.topic || "" };
}

/** Body for POST /api/review/questions/{id}/subject, or null when nothing changed. Only changed fields are sent:
 * a topic-only change must not turn the source subject into an admin override. "" → null (follow the source). */
export function subjectChanges(edit: SubjectFields, base: SubjectFields): { subject_code?: string | null; topic?: string | null } | null {
  const out: { subject_code?: string | null; topic?: string | null } = {};
  if (edit.subject !== base.subject) out.subject_code = edit.subject || null;
  if (edit.topic.trim() !== base.topic.trim()) out.topic = edit.topic.trim() || null;
  return Object.keys(out).length ? out : null;
}
