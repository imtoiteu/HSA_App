"""practice plans (FREE/PRO), PRO subscriptions, immutable order snapshots + event history, exam access

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.models import ORDER_IMMUTABILITY_SQL

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'plan',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(16), nullable=False, unique=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.Text()),
        sa.Column('price_vnd', sa.Integer(), nullable=False),
        sa.Column('duration_days', sa.Integer()),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('features', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.CheckConstraint('price_vnd >= 0', name='ck_plan_price'),
        sa.CheckConstraint('duration_days is null or duration_days > 0', name='ck_plan_duration'),
    )

    # orders: kind, immutable reference/destination snapshot, failed state
    op.add_column('payment_order', sa.Column('kind', sa.String(16), server_default='product', nullable=False))
    op.add_column('payment_order', sa.Column('plan_code', sa.String(16)))
    op.add_column('payment_order', sa.Column('list_price_vnd', sa.Integer()))
    op.add_column('payment_order', sa.Column('transfer_content', sa.String(40)))
    op.add_column('payment_order', sa.Column('bank_snapshot', postgresql.JSONB(astext_type=sa.Text())))
    op.add_column('payment_order', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')))
    op.execute("UPDATE payment_order SET kind = CASE WHEN product_snapshot->>'kind' = 'attempt' THEN 'exam' "
               "ELSE 'product' END, list_price_vnd = amount_vnd, transfer_content = code")
    op.drop_constraint('ck_order_status', 'payment_order', type_='check')
    op.create_check_constraint('ck_order_status', 'payment_order',
                               "status in ('pending','paid','expired','cancelled','failed','refunded')")
    op.create_check_constraint('ck_order_kind', 'payment_order', "kind in ('pro','exam','product')")
    for stmt in ORDER_IMMUTABILITY_SQL:
        op.execute(stmt)

    op.create_table(
        'payment_order_event',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('payment_order.id'), nullable=False),
        sa.Column('from_status', sa.String(16)),
        sa.Column('to_status', sa.String(16)),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('actor_user_id', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('note', sa.Text()),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_payment_order_event_order_id', 'payment_order_event', ['order_id'])
    # history of existing orders: creation and their current state
    op.execute("INSERT INTO payment_order_event (order_id, from_status, to_status, source, note, data, created_at) "
               "SELECT id, NULL, 'pending', 'user', 'created (before history was recorded)', '{}'::jsonb, created_at "
               "FROM payment_order")
    op.execute("INSERT INTO payment_order_event (order_id, from_status, to_status, source, actor_user_id, note, data, "
               "created_at) SELECT id, 'pending', status, CASE WHEN confirmed_by IS NOT NULL THEN 'admin' ELSE 'system' "
               "END, confirmed_by, 'state before history was recorded', '{}'::jsonb, coalesce(paid_at, now()) "
               "FROM payment_order WHERE status <> 'pending'")

    op.create_table(
        'plan_subscription',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('app_user.id'), nullable=False),
        sa.Column('plan_code', sa.String(16), nullable=False),
        sa.Column('plan_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('order_id', sa.BigInteger(), sa.ForeignKey('payment_order.id'), unique=True),
        sa.Column('granted_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('revoked_by', sa.BigInteger(), sa.ForeignKey('app_user.id')),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('revoke_reason', sa.Text()),
        sa.Column('note', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status in ('active','revoked')", name='ck_sub_status'),
        sa.CheckConstraint("source in ('order','admin_grant')", name='ck_sub_source'),
        sa.CheckConstraint('expires_at > starts_at', name='ck_sub_period'),
    )
    op.create_index('ix_plan_subscription_user_id', 'plan_subscription', ['user_id'])
    op.create_index('ix_sub_user_period', 'plan_subscription', ['user_id', 'status', 'expires_at'])

    # mock exams: explicit free/paid, promotion, attempts per purchase
    op.add_column('exam_blueprint', sa.Column('access', sa.String(8), server_default='free', nullable=False))
    op.add_column('exam_blueprint', sa.Column('promo_price_vnd', sa.Integer()))
    op.add_column('exam_blueprint', sa.Column('attempts_per_purchase', sa.Integer(), server_default='1',
                                              nullable=False))
    op.execute("UPDATE exam_blueprint SET access = 'paid' WHERE price_vnd > 0")
    op.create_check_constraint('ck_bp_access', 'exam_blueprint', "access in ('free','paid')")
    op.create_check_constraint('ck_bp_promo', 'exam_blueprint', 'promo_price_vnd is null or promo_price_vnd > 0')
    op.create_check_constraint('ck_bp_attempts', 'exam_blueprint', 'attempts_per_purchase between 1 and 100')

    # practice usage by plan (submitted sessions are frozen by a trigger, so only new rows get a value)
    op.add_column('exam_session', sa.Column('access_plan', sa.String(8)))


def downgrade():
    op.drop_column('exam_session', 'access_plan')
    op.drop_constraint('ck_bp_attempts', 'exam_blueprint', type_='check')
    op.drop_constraint('ck_bp_promo', 'exam_blueprint', type_='check')
    op.drop_constraint('ck_bp_access', 'exam_blueprint', type_='check')
    # a paid exam priced by the default price keeps being paid in the old model only with a price
    op.execute("UPDATE exam_blueprint SET price_vnd = coalesce(promo_price_vnd, 20000) "
               "WHERE access = 'paid' AND price_vnd = 0")
    op.execute("UPDATE exam_blueprint SET price_vnd = 0 WHERE access = 'free'")
    op.drop_column('exam_blueprint', 'attempts_per_purchase')
    op.drop_column('exam_blueprint', 'promo_price_vnd')
    op.drop_column('exam_blueprint', 'access')
    op.drop_index('ix_sub_user_period', table_name='plan_subscription')
    op.drop_index('ix_plan_subscription_user_id', table_name='plan_subscription')
    op.drop_table('plan_subscription')
    op.drop_index('ix_payment_order_event_order_id', table_name='payment_order_event')
    op.drop_table('payment_order_event')
    op.execute("DROP TRIGGER IF EXISTS trg_order_immutable ON payment_order")
    op.execute("DROP FUNCTION IF EXISTS hsa_order_immutable()")
    op.drop_constraint('ck_order_kind', 'payment_order', type_='check')
    op.drop_constraint('ck_order_status', 'payment_order', type_='check')
    # 'failed' does not exist in the old model; such orders were never paid
    op.execute("UPDATE payment_order SET status = 'cancelled' WHERE status = 'failed'")
    op.create_check_constraint('ck_order_status', 'payment_order',
                               "status in ('pending','paid','expired','cancelled','refunded')")
    op.drop_column('payment_order', 'updated_at')
    op.drop_column('payment_order', 'bank_snapshot')
    op.drop_column('payment_order', 'transfer_content')
    op.drop_column('payment_order', 'list_price_vnd')
    op.drop_column('payment_order', 'plan_code')
    op.drop_column('payment_order', 'kind')
    op.drop_table('plan')
