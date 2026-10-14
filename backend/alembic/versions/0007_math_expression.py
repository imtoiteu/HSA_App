"""Answer type "Biểu thức / Công thức toán" (math_expression): an expression graded by algebraic equivalence.

The key lives in question_version.answer ({"kind": "math_expression", "tex", "accepted", "variables",
"tolerance"}); no column changes. question.answer_kind holds the new kind (String(16) fits it). Items of a
multi-part question entered as "formula" keys before this revision are counted under the new kind.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14
"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE question SET answer_kind = 'math_expression' WHERE answer_kind = 'formula'")


def downgrade():
    pass   # the kind is only a label of the answer document; nothing to undo
