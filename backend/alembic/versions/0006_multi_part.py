"""Multi-part questions ("Nhiều ý / Câu ghép"): question type multi_part.

The type lives in question.question_type / question_version.content (no new column: the items and their
answers are part of the version's content and answer documents, per-item results are derived from the frozen
answer and the student's response). A stored serving policy that lists its allowed types explicitly gets the
new type, so a multi-part question approved in the Review Studio can be served.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14
"""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE app_setting
           SET value = jsonb_set(value, '{allowed_types}', (value->'allowed_types') || '["multi_part"]'::jsonb)
         WHERE key = 'serving_policy'
           AND jsonb_typeof(value->'allowed_types') = 'array'
           AND NOT (value->'allowed_types') ? 'multi_part'
    """)


def downgrade():
    op.execute("""
        UPDATE app_setting
           SET value = jsonb_set(value, '{allowed_types}', (value->'allowed_types') - 'multi_part')
         WHERE key = 'serving_policy' AND jsonb_typeof(value->'allowed_types') = 'array'
    """)
