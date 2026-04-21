"""initial_schema

Revision ID: 0001
Revises: 
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'feed_sources',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('url', sa.String(2048), unique=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('active', sa.Boolean, default=True),
        sa.Column('fetch_interval_minutes', sa.Integer, default=60),
        sa.Column('last_fetched_at', sa.DateTime, nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('consecutive_errors', sa.Integer, default=0),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        'summaries',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('period', sa.String(20), nullable=False),
        sa.Column('date', sa.Date, nullable=False),
        sa.Column('summary_text', sa.Text, nullable=False),
        sa.Column('key_takeaways', sa.JSON, nullable=False),
        sa.Column('source_article_ids', sa.JSON, nullable=False),
        sa.Column('model_used', sa.String(50), nullable=False),
        sa.Column('token_count', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('sent_at', sa.DateTime, nullable=True),
        sa.UniqueConstraint('category', 'period', 'date', name='uq_summary_category_period_date'),
    )

    op.create_table(
        'news_articles',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('source_id', sa.Integer, sa.ForeignKey('feed_sources.id'), nullable=False),
        sa.Column('url', sa.String(2048), unique=True, nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('raw_content', sa.Text, nullable=True),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('published_at', sa.DateTime, nullable=False),
        sa.Column('fetched_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('processed', sa.Boolean, default=False),
        sa.Column('summary_id', sa.Integer, sa.ForeignKey('summaries.id'), nullable=True),
        sa.Column('content_hash', sa.String(64), nullable=False),
    )

    op.create_table(
        'subscribers',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('phone_number', sa.String(20), unique=True, nullable=False),
        sa.Column('name', sa.String(100), nullable=True),
        sa.Column('active', sa.Boolean, default=True),
        sa.Column('preferences', sa.JSON, default={}),
        sa.Column('last_sent_at', sa.DateTime, nullable=True),
        sa.Column('timezone', sa.String(50), default='America/Sao_Paulo'),
        sa.Column('subscribed_at', sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        'user_interactions',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('subscriber_id', sa.Integer, sa.ForeignKey('subscribers.id'), nullable=False),
        sa.Column('incoming_message', sa.Text, nullable=False),
        sa.Column('message_type', sa.String(20), nullable=False),
        sa.Column('command', sa.String(50), nullable=True),
        sa.Column('response_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        'delivery_log',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('subscriber_id', sa.Integer, sa.ForeignKey('subscribers.id'), nullable=False),
        sa.Column('summary_id', sa.Integer, sa.ForeignKey('summaries.id'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        'pipeline_runs',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('period', sa.String(20), nullable=False),
        sa.Column('date', sa.Date, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('articles_collected', sa.Integer, default=0),
        sa.Column('summaries_generated', sa.Integer, default=0),
        sa.Column('messages_sent', sa.Integer, default=0),
        sa.Column('error_log', sa.Text, nullable=True),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('finished_at', sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table('pipeline_runs')
    op.drop_table('delivery_log')
    op.drop_table('user_interactions')
    op.drop_table('subscribers')
    op.drop_table('news_articles')
    op.drop_table('summaries')
    op.drop_table('feed_sources')
