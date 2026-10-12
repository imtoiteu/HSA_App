"""Review Studio: staff roles, review state per question, review history, validator findings, assignments,
source-document cache; question.upstream_version_id / review_gate; question_version.origin

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade():
    op.drop_constraint('ck_user_role', 'app_user', type_='check')
    op.create_check_constraint('ck_user_role', 'app_user',
                               "role in ('student','admin','editor','reviewer','approver')")

    op.add_column('question_version', sa.Column('origin', sa.String(12), server_default='upstream', nullable=False))
    op.add_column('question_version', sa.Column('created_by', sa.BigInteger(), sa.ForeignKey('app_user.id')))
    op.add_column('question_version', sa.Column('note', sa.Text()))

    op.add_column('question', sa.Column('upstream_version_id', sa.BigInteger()))
    op.create_foreign_key('fk_question_upstream_version', 'question', 'question_version',
                          ['upstream_version_id'], ['id'])
    op.add_column('question', sa.Column('review_gate', sa.String(12)))
    op.create_check_constraint('ck_q_review_gate', 'question',
                               "review_gate is null or review_gate in ('ready','blocked')")
    op.execute("CREATE INDEX ix_question_document ON question ((provenance->>'document_id'))")
    op.execute("CREATE INDEX ix_question_review_gate ON question (review_gate) WHERE review_gate IS NOT NULL")
    # every existing version came from upstream
    op.execute("UPDATE question SET upstream_version_id = current_version_id")

    op.create_table(
        'review_assignment',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('label', sa.String(200), nullable=False),
        sa.Column('reviewer_id', sa.BigInteger(), sa.ForeignKey('app_user.id'), nullable=False, index=True),
        sa.Column('queue', sa.String(24)),
        sa.Column('subject_code', sa.String(40)),
        sa.Column('filter', JSONB, nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('created_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
    )
    op.create_table(
        'question_review',
        sa.Column('question_id', sa.BigInteger(), sa.ForeignKey('question.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('status', sa.String(12), server_default='none', nullable=False),
        sa.Column('queues', postgresql.ARRAY(sa.String(24)), server_default='{}', nullable=False),
        sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
        sa.Column('draft_version_id', sa.BigInteger(), sa.ForeignKey('question_version.id')),
        sa.Column('dims', JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('hashes', JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('content_hash', sa.String(64)),
        sa.Column('validator_version', sa.Integer()),
        sa.Column('validated_hash', sa.String(64)),
        sa.Column('validated_at', TS),
        sa.Column('findings_open', sa.Integer(), server_default='0', nullable=False),
        sa.Column('findings_critical', sa.Integer(), server_default='0', nullable=False),
        sa.Column('upstream_changed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('dim_state', JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('dirty', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('dup_exact', sa.String(64)),
        sa.Column('dup_near', sa.String(64)),
        sa.Column('source_page', sa.Integer()),
        sa.Column('external_id', sa.String(80), server_default='', nullable=False),
        sa.Column('subject_code', sa.String(40), server_default='', nullable=False),
        sa.Column('qtype', sa.String(40), server_default='', nullable=False),
        sa.Column('served', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('doc_id', sa.String(40), server_default='', nullable=False),
        sa.Column('doc_path', sa.String(200), server_default='', nullable=False),
        sa.Column('source_number', sa.Integer(), server_default='0', nullable=False),
        sa.Column('search_text', sa.Text(), server_default='', nullable=False),
        sa.Column('state', sa.String(40), server_default='', nullable=False),
        sa.Column('gate', sa.String(12), server_default='', nullable=False),
        sa.Column('assignee_id', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('assignment_id', sa.Integer(), sa.ForeignKey('review_assignment.id')),
        sa.Column('assigned_at', TS),
        sa.Column('reviewed_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('reviewed_at', TS),
        sa.Column('approved_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('approved_at', TS),
        sa.Column('verified_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('verified_at', TS),
        sa.Column('verification_method', sa.String(24)),
        sa.Column('note', sa.Text()),
        sa.Column('updated_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('updated_at', TS),
        sa.CheckConstraint("status in ('none','draft','reviewed','approved','returned')", name='ck_review_status'),
    )
    op.create_index('ix_review_queues', 'question_review', ['queues'], postgresql_using='gin')
    op.create_index('ix_review_assignee', 'question_review', ['assignee_id', 'status'])
    op.create_index('ix_review_status', 'question_review', ['status'])
    op.create_index('ix_review_updated', 'question_review', ['updated_at'])
    op.create_index('ix_review_dirty', 'question_review', ['dirty'], postgresql_where=sa.text('dirty'))
    op.create_index('ix_review_dup_exact', 'question_review', ['dup_exact'])
    op.create_index('ix_review_dup_near', 'question_review', ['dup_near'])
    # one row per question with its filter columns (queues/validators are filled by `hsa-app review-refresh`)
    op.execute("""
        INSERT INTO question_review (question_id, external_id, subject_code, qtype, served, active, doc_id, doc_path,
                                     source_number, state, queues)
        SELECT q.id, q.external_id, coalesce(q.subject_code, ''), q.question_type, q.is_served,
               b.is_active AND NOT q.removed_upstream, coalesce(q.provenance->>'document_id', ''),
               right(coalesce(q.provenance->>'document_path', ''), 200),
               coalesce(CASE WHEN q.provenance->>'source_question_number' ~ '^[0-9]{1,6}$'
                             THEN (q.provenance->>'source_question_number')::int END, 0),
               q.editorial_state,
               CASE WHEN q.editorial_state = 'READY_TO_SERVE' THEN ARRAY['READY'] ELSE ARRAY['NOT_READY'] END
        FROM question q JOIN question_bank b ON b.id = q.bank_id""")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index('ix_review_ext', 'question_review', ['external_id'])
    op.create_index('ix_review_prio', 'question_review', [sa.text('priority DESC'), 'question_id'])
    op.create_index('ix_review_doc', 'question_review', ['doc_path', 'source_page', 'source_number', 'question_id'])
    op.create_index('ix_review_search', 'question_review', ['search_text'], postgresql_using='gin',
                    postgresql_ops={'search_text': 'gin_trgm_ops'})

    op.create_table(
        'review_event',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('question_id', sa.BigInteger(), sa.ForeignKey('question.id'), nullable=False, index=True),
        sa.Column('external_id', sa.String(80), nullable=False),
        sa.Column('actor_id', sa.BigInteger(), sa.ForeignKey('app_user.id'), index=True),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('changes', JSONB, nullable=False),
        sa.Column('note', sa.Text()),
        sa.Column('source_ref', JSONB),
        sa.Column('from_version_id', sa.BigInteger(), sa.ForeignKey('question_version.id')),
        sa.Column('to_version_id', sa.BigInteger(), sa.ForeignKey('question_version.id')),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False, index=True),
    )
    op.create_table(
        'review_finding',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('question_id', sa.BigInteger(), sa.ForeignKey('question.id', ondelete='CASCADE'), nullable=False,
                  index=True),
        sa.Column('code', sa.String(40), nullable=False),
        sa.Column('severity', sa.String(10), nullable=False),
        sa.Column('dimension', sa.String(12), nullable=False),
        sa.Column('detail', sa.Text()),
        sa.Column('part_hash', sa.String(64)),
        sa.Column('validator_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(12), nullable=False),
        sa.Column('reason', sa.Text()),
        sa.Column('resolved_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('resolved_at', TS),
        sa.Column('created_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', TS),
        sa.UniqueConstraint('question_id', 'code', name='uq_finding_code'),
        sa.CheckConstraint("status in ('open','accepted','fixed','dismissed')", name='ck_finding_status'),
    )
    op.create_index('ix_finding_code_status', 'review_finding', ['code', 'status'])
    op.create_table(
        'source_cache',
        sa.Column('document_id', sa.String(40), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('file_kind', sa.String(8)),
        sa.Column('pages', sa.Integer()),
        sa.Column('blob_sha256', sa.String(64)),
        sa.Column('detail', sa.Text()),
        sa.Column('requested_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('requested_at', TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('ready_at', TS),
    )


def downgrade():
    op.drop_table('source_cache')
    op.drop_index('ix_finding_code_status', 'review_finding')
    op.drop_table('review_finding')
    op.drop_table('review_event')
    op.drop_table('question_review')
    op.drop_table('review_assignment')
    # published review versions stay in question_version (immutable, referenced by exam history); the current
    # pointer keeps them, only the review metadata columns go away
    op.execute("DROP INDEX IF EXISTS ix_question_document")
    op.execute("DROP INDEX IF EXISTS ix_question_review_gate")
    op.drop_constraint('ck_q_review_gate', 'question', type_='check')
    op.drop_column('question', 'review_gate')
    op.drop_constraint('fk_question_upstream_version', 'question', type_='foreignkey')
    op.drop_column('question', 'upstream_version_id')
    op.drop_column('question_version', 'note')
    op.drop_column('question_version', 'created_by')
    op.drop_column('question_version', 'origin')
    op.execute("UPDATE app_user SET role = 'student' WHERE role IN ('editor','reviewer','approver')")
    op.drop_constraint('ck_user_role', 'app_user', type_='check')
    op.create_check_constraint('ck_user_role', 'app_user', "role in ('student','admin')")
