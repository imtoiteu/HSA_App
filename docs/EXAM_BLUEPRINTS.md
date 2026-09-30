# Exam blueprints

An exam format is **data** (`exam_blueprint.config`, edited in Admin → Cấu trúc đề), validated by
`backend/app/exam/blueprint.py`. Changing a blueprint bumps its `version`; every session keeps a
snapshot of the exact configuration it was generated from.

```jsonc
{
  "sections": [
    {
      "key": "toan",                              // unique [a-z0-9_]
      "title": "Phần 1: Toán học và Xử lý số liệu",
      "description": "50 câu · 75 phút",
      "duration_minutes": 75,                     // required when timing = per_section
      "points_per_question": 1,
      "order": "pool",                            // pool (pool order) | shuffled (seeded shuffle of units)
      "items": [{"external_id": "cq_…", "bank": "hsa"}],  // optional: exact questions first, in this order
      "pools": [                                  // … plus/or random selection
        {"subjects": ["math"], "types": ["single_choice"], "count": 40,
         "exam_systems": [], "topics": [], "banks": [], "cognitive_levels": []},
        {"subjects": ["math"], "types": ["numeric_response"], "count": 10}
      ]
    }
  ],
  "timing": "per_section",          // none | global | per_section
  "duration_minutes": null,         // required when timing = global
  "shuffle_options": true,          // only for questions whose options don't refer to each other
  "keep_groups_together": true,     // passage groups are selected/served as one unit
  "max_group_size": 8,
  "require_auto_scoring": true,     // exams only use deterministically scorable questions
  "feedback": "end",                // end | immediate
  "allow_review": true,             // show keys & solutions after submission
  "scoring": {
    "correct": 1, "incorrect": 0, "unanswered": 0,   // multipliers of points (negative marking possible)
    "multi_choice": "all_or_nothing",                // | partial
    "tf_sequence": "all_or_nothing",                 // | thpt2025 (0.1/0.25/0.5/1) | per_statement
    "numeric_tolerance": 1e-6,                       // relative
    "scale_to": 150                                  // report a scaled score (optional)
  }
}
```

## Seeded formats (from the documented HSA structure)

The HSA exam (ĐHQG Hà Nội) has three consecutive parts, 150 questions, 195 minutes, 150 points:
Toán học & Xử lý số liệu (50 câu / 75'), Văn học – Ngôn ngữ (50 / 60'), and Khoa học **or**
Tiếng Anh (50 / 60'). The seed creates both full variants (per-section timing, scale 150, priced
20 000 VND per attempt as an example), each part on its own (free) and a 30-question mini test.
Prices, publication and structure are all editable; the admin editor shows, per pool, how many
served questions are available before publishing.

## Determinism and reproducibility

Selection orders candidate units by `sha256(seed, section, pool, unit)`; option order by
`sha256(seed, "opt", question, label)`. A session stores seed, generator version, blueprint
snapshot, scoring snapshot, pool fingerprint and the exact `question_version_id` + option order of
every item — reviews never regenerate anything.

## Curated exam sets

A section may combine **fixed items** (hand-picked questions in a manual order, placed first) with
**random pools** (subject/type quotas, bank selection, exam-system, topic and cognitive-level filters
where the bank provides them); random picks never repeat a fixed item. Price, publication and access
are set per blueprint; products can bundle attempts across blueprints. Everything is data — new exam
sets need no code changes. Questions from inactive banks are never selected.
