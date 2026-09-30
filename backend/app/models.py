"""Database schema. Alembic migrations in `alembic/versions` must mirror this module."""
import datetime as dt
import uuid

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


TS = DateTime(timezone=True)


# ------------------------------------------------------------------------------------------------
# users & auth
# ------------------------------------------------------------------------------------------------
class User(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-cased
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="student")  # student | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    last_login_at: Mapped[dt.datetime | None] = mapped_column(TS)
    __table_args__ = (CheckConstraint("role in ('student','admin')", name="ck_user_role"),)


class AuthSession(Base):
    __tablename__ = "auth_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(TS)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(TS)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    user: Mapped[User] = relationship()


class PasswordReset(Base):
    __tablename__ = "password_reset"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(TS)
    used_at: Mapped[dt.datetime | None] = mapped_column(TS)


class RateLimit(Base):
    __tablename__ = "rate_limit"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[dt.datetime] = mapped_column(TS, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------------------------------------------
# content
# ------------------------------------------------------------------------------------------------
class QuestionBank(Base):
    __tablename__ = "question_bank"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[str] = mapped_column(String(40))  # hsa_upstream | jsonl_import | manual
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(80))  # upstream snapshot / dataset version label
    source_uri: Mapped[str | None] = mapped_column(Text)  # where it comes from (path, repository, file)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # inactive banks are never served
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(TS)


class Subject(Base):
    __tablename__ = "subject"
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    short_name: Mapped[str | None] = mapped_column(String(40))
    color: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    practice_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SubjectAlias(Base):
    """Maps a bank's own subject value (e.g. upstream 'literature_language') to an app subject."""
    __tablename__ = "subject_alias"
    bank_id: Mapped[int] = mapped_column(ForeignKey("question_bank.id", ondelete="CASCADE"), primary_key=True)
    source_value: Mapped[str] = mapped_column(String(80), primary_key=True)
    subject_code: Mapped[str] = mapped_column(ForeignKey("subject.code"))


class Asset(Base):
    """A file in the content-addressed media store (media_root/<sha[:2]>/<sha>.<ext>)."""
    __tablename__ = "asset"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    ext: Mapped[str] = mapped_column(String(8))
    mime: Mapped[str] = mapped_column(String(40))
    bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    source_ref: Mapped[str | None] = mapped_column(String(80))  # e.g. upstream as_… id
    derived_from: Mapped[str | None] = mapped_column(String(64))  # sha of original (e.g. WMF) when converted
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())

    @property
    def rel_path(self) -> str:
        return f"{self.sha256[:2]}/{self.sha256}.{self.ext}"


class Question(Base):
    __tablename__ = "question"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_id: Mapped[int] = mapped_column(ForeignKey("question_bank.id"))
    external_id: Mapped[str] = mapped_column(String(80))  # cq_… for the HSA bank
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("question_version.id", use_alter=True,
                                                                      name="fk_question_current_version"))
    question_type: Mapped[str] = mapped_column(String(40))
    subject_code: Mapped[str | None] = mapped_column(ForeignKey("subject.code"))
    source_subject: Mapped[str | None] = mapped_column(String(80))  # original subject in the bank
    # second-pass subject inference (upstream editorial/subject_inference.jsonl); kept separate
    inferred_subject: Mapped[str | None] = mapped_column(String(80))
    inference_confidence: Mapped[str | None] = mapped_column(String(16))  # high | medium | …
    inference_evidence: Mapped[list | None] = mapped_column(JSONB)
    # upstream's validated final subject decision (editorial/subject_effective.jsonl), when available
    upstream_effective_subject: Mapped[str | None] = mapped_column(String(80))
    classification_source: Mapped[str | None] = mapped_column(String(40))  # original | second_pass | semantic_…
    classification_confidence: Mapped[str | None] = mapped_column(String(16))
    classification_evidence: Mapped[list | None] = mapped_column(JSONB)
    classification_review: Mapped[str | None] = mapped_column(String(60))  # e.g. SUBJECT_CLASSIFICATION_REVIEW
    # admin editorial layer (app-side only): forces the effective subject
    subject_override: Mapped[str | None] = mapped_column(ForeignKey("subject.code"))
    subject_override_note: Mapped[str | None] = mapped_column(Text)
    # where subject_code came from: admin | upstream_effective | original | inferred | unclassified
    subject_source: Mapped[str] = mapped_column(String(24), default="original", server_default="original")
    topic: Mapped[str | None] = mapped_column(String(200))
    subtopic: Mapped[str | None] = mapped_column(String(200))
    cognitive_level: Mapped[str | None] = mapped_column(String(8))
    language: Mapped[str | None] = mapped_column(String(8))
    group_key: Mapped[str | None] = mapped_column(String(80))  # grp_… (members served together)
    exam_systems: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    has_image: Mapped[bool] = mapped_column(Boolean, default=False)
    has_formula: Mapped[bool] = mapped_column(Boolean, default=False)
    has_table: Mapped[bool] = mapped_column(Boolean, default=False)
    # upstream quality
    review_status: Mapped[str | None] = mapped_column(String(20))
    review_flags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    answer_source_type: Mapped[str | None] = mapped_column(String(40))
    editorial_state: Mapped[str] = mapped_column(String(40))  # primary state
    editorial_states: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    state_source: Mapped[str] = mapped_column(String(24), default="derived")  # upstream_manifest | derived | import
    state_notes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # app serving policy (recomputed by `sync` and `recompute-policy`)
    scoring_mode: Mapped[str] = mapped_column(String(16), default="none")  # auto | self_check | none
    answer_kind: Mapped[str | None] = mapped_column(String(16))  # choice | numeric | text | tf_sequence | boolean
    content_flags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)  # app content checks
    source_hash: Mapped[str | None] = mapped_column(String(64))  # hash of raw upstream inputs (skip unchanged)
    policy_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_reasons: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    # admin decision (never touches upstream): null = follow policy
    admin_override: Mapped[str | None] = mapped_column(String(16))  # enable | disable
    admin_override_note: Mapped[str | None] = mapped_column(Text)
    admin_override_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    admin_override_at: Mapped[dt.datetime | None] = mapped_column(TS)
    # effective serving flag = override ?? policy (materialised for fast pool queries)
    is_served: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[dict] = mapped_column(JSONB, default=dict)
    removed_upstream: Mapped[bool] = mapped_column(Boolean, default=False)
    first_synced_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    last_synced_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    content_changed_at: Mapped[dt.datetime | None] = mapped_column(TS)

    current_version: Mapped["QuestionVersion"] = relationship(foreign_keys=[current_version_id], lazy="joined")

    __table_args__ = (
        UniqueConstraint("bank_id", "external_id", name="uq_question_bank_external"),
        Index("ix_question_pool", "is_served", "subject_code", "question_type"),
        Index("ix_question_inferred_subject", "inferred_subject"),
        Index("ix_question_group", "group_key"),
        Index("ix_question_state", "editorial_state"),
        CheckConstraint("admin_override is null or admin_override in ('enable','disable')", name="ck_q_override"),
        CheckConstraint("scoring_mode in ('auto','self_check','none')", name="ck_q_scoring_mode"),
    )


class QuestionVersion(Base):
    """Immutable render-ready snapshot of a question (content + answer). Never updated or deleted."""
    __tablename__ = "question_version"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSONB)  # stem/options/tables/solution/explanation/group blocks
    answer: Mapped[dict | None] = mapped_column(JSONB)  # normalised answer or null (never inferred)
    source_revision: Mapped[str | None] = mapped_column(String(120))  # upstream snapshot fingerprint
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_run.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (UniqueConstraint("question_id", "content_hash", name="uq_version_hash"),
                      UniqueConstraint("question_id", "version_no", name="uq_version_no"))


class SyncRun(Base):
    __tablename__ = "sync_run"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_id: Mapped[int] = mapped_column(ForeignKey("question_bank.id"))
    started_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | failed | interrupted
    source_fingerprint: Mapped[str | None] = mapped_column(String(200))
    checkpoint: Mapped[str | None] = mapped_column(String(80))  # last external_id fully committed
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str | None] = mapped_column(String(80))


# ------------------------------------------------------------------------------------------------
# exams
# ------------------------------------------------------------------------------------------------
class ExamBlueprint(Base):
    __tablename__ = "exam_blueprint"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="random")  # random | fixed
    config: Mapped[dict] = mapped_column(JSONB)  # validated by exam.blueprint.BlueprintConfig
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on every config change
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    price_vnd: Mapped[int] = mapped_column(Integer, default=0)  # 0 = free; >0 = one attempt costs this
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), onupdate=func.now())
    __table_args__ = (CheckConstraint("price_vnd >= 0", name="ck_bp_price"),)


class ExamSession(Base):
    __tablename__ = "exam_session"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # exam | practice
    title: Mapped[str] = mapped_column(String(200))
    blueprint_id: Mapped[int | None] = mapped_column(ForeignKey("exam_blueprint.id"))
    blueprint_version: Mapped[int | None] = mapped_column(Integer)
    blueprint_snapshot: Mapped[dict] = mapped_column(JSONB)  # the exact config used
    scoring_config: Mapped[dict] = mapped_column(JSONB)
    seed: Mapped[str] = mapped_column(String(64))
    generator_version: Mapped[str] = mapped_column(String(20))
    pool_fingerprint: Mapped[str | None] = mapped_column(String(64))
    feedback: Mapped[str] = mapped_column(String(16), default="end")  # end | immediate
    status: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress | submitted | abandoned
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    started_at: Mapped[dt.datetime] = mapped_column(TS)
    deadline_at: Mapped[dt.datetime | None] = mapped_column(TS)  # global deadline (null = untimed)
    current_section: Mapped[int] = mapped_column(Integer, default=0)
    section_started_at: Mapped[dt.datetime | None] = mapped_column(TS)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(TS)
    submit_reason: Mapped[str | None] = mapped_column(String(16))  # user | timeout
    score: Mapped[float | None] = mapped_column(Float)
    max_score: Mapped[float | None] = mapped_column(Float)
    result: Mapped[dict | None] = mapped_column(JSONB)  # per-section breakdown, counts
    entitlement_id: Mapped[int | None] = mapped_column(ForeignKey("entitlement.id"))
    items: Mapped[list["ExamItem"]] = relationship(order_by="ExamItem.position", lazy="selectin")
    __table_args__ = (
        CheckConstraint("status in ('in_progress','submitted','abandoned')", name="ck_session_status"),
        Index("ix_session_user_status", "user_id", "status"),
    )


class ExamItem(Base):
    __tablename__ = "exam_item"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exam_session.id", ondelete="CASCADE"),
                                                  primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1-based, global order
    section_index: Mapped[int] = mapped_column(Integer, default=0)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id"), index=True)
    question_version_id: Mapped[int] = mapped_column(ForeignKey("question_version.id"))
    option_order: Mapped[list[str] | None] = mapped_column(ARRAY(String(2)))  # canonical labels in display order
    group_key: Mapped[str | None] = mapped_column(String(80))
    points: Mapped[float] = mapped_column(Float, default=1.0)
    scoring_mode: Mapped[str] = mapped_column(String(16), default="auto")
    # filled at submission (or on "check" in immediate-feedback practice)
    answer: Mapped[dict | None] = mapped_column(JSONB)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    time_spent_ms: Mapped[int] = mapped_column(Integer, default=0)
    answered_at: Mapped[dt.datetime | None] = mapped_column(TS)
    checked: Mapped[bool] = mapped_column(Boolean, default=False)  # immediate feedback revealed (locks answer)
    self_assessment: Mapped[str | None] = mapped_column(String(12))  # correct | incorrect (self_check items)
    outcome: Mapped[str | None] = mapped_column(String(12))  # correct | incorrect | unanswered | ungraded
    points_awarded: Mapped[float | None] = mapped_column(Float)
    version: Mapped["QuestionVersion"] = relationship(lazy="joined")


class Bookmark(Base):
    __tablename__ = "bookmark"
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id"), primary_key=True)
    question_version_id: Mapped[int | None] = mapped_column(ForeignKey("question_version.id"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


class QuestionReport(Base):
    __tablename__ = "question_report"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(80))  # immutable cq_… id (denormalised on purpose)
    question_version_id: Mapped[int | None] = mapped_column(ForeignKey("question_version.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String(24))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | triaged | resolved | rejected
    admin_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    resolved_at: Mapped[dt.datetime | None] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (
        CheckConstraint("category in ('wrong_answer','broken_formula','broken_image','typo','unclear','other')",
                        name="ck_report_category"),
        CheckConstraint("status in ('open','triaged','resolved','rejected')", name="ck_report_status"),
    )


class QuestionCorrection(Base):
    """Correction/feedback layer keyed by the immutable external id; exported for upstream reconciliation."""
    __tablename__ = "question_correction"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("question.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(80))
    field: Mapped[str] = mapped_column(String(60))  # stem | option:B | answer | solution | subject | topic | status …
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("question_report.id"))
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | exported | applied | rejected
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    exported_at: Mapped[dt.datetime | None] = mapped_column(TS)


# ------------------------------------------------------------------------------------------------
# commerce
# ------------------------------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "product"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    price_vnd: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int | None] = mapped_column(Integer)  # null = unlimited during validity
    duration_days: Mapped[int | None] = mapped_column(Integer)  # null = no expiry
    blueprint_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)  # empty = every paid exam
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (CheckConstraint("price_vnd > 0", name="ck_product_price"),
                      CheckConstraint("attempts is null or attempts > 0", name="ck_product_attempts"))


class PaymentOrder(Base):
    __tablename__ = "payment_order"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # reference code put in the transfer content
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))  # bundle / pass
    blueprint_id: Mapped[int | None] = mapped_column(ForeignKey("exam_blueprint.id"))  # single attempt of an exam
    product_snapshot: Mapped[dict] = mapped_column(JSONB)  # {name, attempts, duration_days, blueprint_ids}
    amount_vnd: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    provider: Mapped[str | None] = mapped_column(String(24))  # which provider confirmed it
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(TS)
    paid_at: Mapped[dt.datetime | None] = mapped_column(TS)
    paid_amount_vnd: Mapped[int | None] = mapped_column(Integer)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))  # admin for manual
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("status in ('pending','paid','expired','cancelled','refunded')",
                                      name="ck_order_status"),
                      CheckConstraint("amount_vnd > 0", name="ck_order_amount"))


class PaymentTransaction(Base):
    """Every incoming provider notification / manual confirmation. Unique per provider txn → idempotent."""
    __tablename__ = "payment_transaction"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(24))
    provider_txn_id: Mapped[str] = mapped_column(String(120))
    amount_vnd: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[dt.datetime | None] = mapped_column(TS)
    received_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("payment_order.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # matched | unmatched | underpaid | late | ignored
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("provider", "provider_txn_id", name="uq_txn_provider_id"),)


class Entitlement(Base):
    __tablename__ = "entitlement"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("product.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("payment_order.id"), unique=True)
    blueprint_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)  # empty = all paid exams
    attempts_total: Mapped[int | None] = mapped_column(Integer)  # null = unlimited
    attempts_used: Mapped[int] = mapped_column(Integer, default=0)
    valid_from: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    valid_until: Mapped[dt.datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))  # admin grant (no order)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (CheckConstraint("attempts_total is null or attempts_used <= attempts_total",
                                      name="ck_ent_attempts"),)


class EntitlementUsage(Base):
    __tablename__ = "entitlement_usage"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entitlement_id: Mapped[int] = mapped_column(ForeignKey("entitlement.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    blueprint_id: Mapped[int] = mapped_column(ForeignKey("exam_blueprint.id"))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


class SourceDocument(Base):
    """A source document of a bank that produced no servable questions yet (e.g. scanned PDFs deferred
    upstream as NEEDS_MATH_AWARE_OCR). Tracked so the bank's coverage is accounted for; never served."""
    __tablename__ = "source_document"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bank_id: Mapped[int] = mapped_column(ForeignKey("question_bank.id"))
    external_id: Mapped[str] = mapped_column(String(80))
    path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40))
    pages: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_synced_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (UniqueConstraint("bank_id", "external_id", name="uq_source_document"),)


class FormulaCheck(Base):
    """Result of rendering a LaTeX string with the web renderer (KaTeX), keyed by sha256(tex)."""
    __tablename__ = "formula_check"
    tex_sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    tex: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    renderer: Mapped[str] = mapped_column(String(40))
    checked_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


# ------------------------------------------------------------------------------------------------
# ops
# ------------------------------------------------------------------------------------------------
class AppSetting(Base):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), onupdate=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now(), index=True)


# Immutability triggers (installed by the initial migration; see alembic/versions/0001_initial.py)
IMMUTABILITY_SQL = [
    """
    CREATE OR REPLACE FUNCTION hsa_forbid_change() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION '% on % is not allowed: rows are immutable', TG_OP, TG_TABLE_NAME;
    END; $$ LANGUAGE plpgsql;
    """,
    "CREATE TRIGGER trg_question_version_immutable BEFORE UPDATE OR DELETE ON question_version "
    "FOR EACH ROW EXECUTE FUNCTION hsa_forbid_change();",
    """
    CREATE OR REPLACE FUNCTION hsa_freeze_submitted_session() RETURNS trigger AS $$
    BEGIN
      IF OLD.status = 'submitted' THEN
        RAISE EXCEPTION 'exam session % is submitted and frozen', OLD.id;
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    """,
    "CREATE TRIGGER trg_session_frozen BEFORE UPDATE OR DELETE ON exam_session "
    "FOR EACH ROW EXECUTE FUNCTION hsa_freeze_submitted_session();",
    """
    CREATE OR REPLACE FUNCTION hsa_freeze_submitted_item() RETURNS trigger AS $$
    DECLARE st text;
    BEGIN
      SELECT status INTO st FROM exam_session WHERE id = OLD.session_id;
      IF st = 'submitted' THEN
        RAISE EXCEPTION 'items of submitted exam session % are frozen', OLD.session_id;
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      IF NEW.question_version_id <> OLD.question_version_id OR NEW.position <> OLD.position
         OR NEW.option_order IS DISTINCT FROM OLD.option_order THEN
        RAISE EXCEPTION 'exam item composition is immutable';
      END IF;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
    """,
    "CREATE TRIGGER trg_item_frozen BEFORE UPDATE OR DELETE ON exam_item "
    "FOR EACH ROW EXECUTE FUNCTION hsa_freeze_submitted_item();",
]
