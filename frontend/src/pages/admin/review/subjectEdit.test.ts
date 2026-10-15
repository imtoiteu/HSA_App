import { describe, expect, it } from "vitest";
import { subjectChanges, subjectFields } from "./subjectEdit";

describe("subject edit", () => {
  const base = subjectFields({ subject: "math", subject_override: null, topic: "Logarit" });

  it("starts from the override, else the effective subject", () => {
    expect(base).toEqual({ subject: "math", topic: "Logarit" });
    expect(subjectFields({ subject: "physics", subject_override: "physics", topic: null })).toEqual({ subject: "physics", topic: "" });
  });

  it("nothing changed → nothing to send (the question is not dirty)", () => {
    expect(subjectChanges({ ...base }, base)).toBeNull();
    expect(subjectChanges({ ...base, topic: " Logarit " }, base)).toBeNull();
  });

  it("a changed dropdown is sent as the new subject", () => {
    expect(subjectChanges({ ...base, subject: "physics" }, base)).toEqual({ subject_code: "physics" });
  });

  it("back to 'Theo nguồn' clears the override", () => {
    const over = subjectFields({ subject: "physics", subject_override: "physics", topic: null });
    expect(subjectChanges({ ...over, subject: "" }, over)).toEqual({ subject_code: null });
  });

  it("a topic-only change does not send the subject", () => {
    expect(subjectChanges({ ...base, topic: "Mũ" }, base)).toEqual({ topic: "Mũ" });
    expect(subjectChanges({ ...base, topic: "" }, base)).toEqual({ topic: null });
  });
});
