"""add indexes for frequently queried columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_news_articles_category', 'news_articles', ['category'])
    op.create_index('ix_news_articles_published_at', 'news_articles', ['published_at'])
    op.create_index('ix_news_articles_content_hash', 'news_articles', ['content_hash'])
    op.create_index('ix_news_articles_source_id', 'news_articles', ['source_id'])

    op.create_index('ix_summaries_date', 'summaries', ['date'])
    op.create_index('ix_summaries_category', 'summaries', ['category'])
    op.create_index('ix_summaries_created_at', 'summaries', ['created_at'])

    op.create_index('ix_user_interactions_subscriber_id', 'user_interactions', ['subscriber_id'])
    op.create_index('ix_user_interactions_created_at', 'user_interactions', ['created_at'])

    op.create_index('ix_delivery_log_subscriber_id', 'delivery_log', ['subscriber_id'])
    op.create_index('ix_delivery_log_summary_id', 'delivery_log', ['summary_id'])

    op.create_index('ix_pipeline_runs_date', 'pipeline_runs', ['date'])
    op.create_index('ix_pipeline_runs_period', 'pipeline_runs', ['period'])

    op.create_index('ix_feed_sources_category', 'feed_sources', ['category'])
    op.create_index('ix_feed_sources_active', 'feed_sources', ['active'])


def downgrade() -> None:
    op.drop_index('ix_feed_sources_active')
    op.drop_index('ix_feed_sources_category')
    op.drop_index('ix_pipeline_runs_period')
    op.drop_index('ix_pipeline_runs_date')
    op.drop_index('ix_delivery_log_summary_id')
    op.drop_index('ix_delivery_log_subscriber_id')
    op.drop_index('ix_user_interactions_created_at')
    op.drop_index('ix_user_interactions_subscriber_id')
    op.drop_index('ix_summaries_created_at')
    op.drop_index('ix_summaries_category')
    op.drop_index('ix_summaries_date')
    op.drop_index('ix_news_articles_source_id')
    op.drop_index('ix_news_articles_content_hash')
    op.drop_index('ix_news_articles_published_at')
    op.drop_index('ix_news_articles_category')
