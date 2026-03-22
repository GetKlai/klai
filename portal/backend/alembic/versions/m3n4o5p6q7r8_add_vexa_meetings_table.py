"""add vexa_meetings table

Revision ID: m3n4o5p6q7r8
Revises: l2m3n4o5p6q7
Create Date: 2026-03-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'm3n4o5p6q7r8'
down_revision = 'l2m3n4o5p6q7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'vexa_meetings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('zitadel_user_id', sa.String(64), nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('portal_orgs.id'), nullable=True),
        sa.Column('platform', sa.String(32), nullable=False),
        sa.Column('native_meeting_id', sa.String(128), nullable=False),
        sa.Column('meeting_url', sa.Text(), nullable=False),
        sa.Column('meeting_title', sa.String(255), nullable=True),
        sa.Column('bot_id', sa.String(128), nullable=True),
        sa.Column('status', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('consent_given', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('transcript_text', sa.Text(), nullable=True),
        sa.Column('transcript_segments', postgresql.JSONB(), nullable=True),
        sa.Column('language', sa.String(16), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_vexa_meetings_zitadel_user_id', 'vexa_meetings', ['zitadel_user_id'])
    op.create_index('ix_vexa_meetings_status', 'vexa_meetings', ['status'])


def downgrade() -> None:
    op.drop_index('ix_vexa_meetings_status')
    op.drop_index('ix_vexa_meetings_zitadel_user_id')
    op.drop_table('vexa_meetings')
