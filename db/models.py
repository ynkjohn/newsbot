import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    fetch_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    last_fetched_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    articles: Mapped[list["NewsArticle"]] = relationship(back_populates="source")


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("feed_sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    published_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    summary_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("summaries.id"), nullable=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trust_status: Mapped[str] = mapped_column(String(20), default="trusted")
    trust_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source: Mapped["FeedSource"] = relationship(back_populates="articles")
    summary: Mapped[Optional["Summary"]] = relationship(back_populates="articles")


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (
        UniqueConstraint("category", "period", "date", name="uq_summary_category_period_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)  # "morning", "midday", "afternoon", or "evening"
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_takeaways: Mapped[dict] = mapped_column(JSON, nullable=False)  # {"bullets": list[str], "insight": str}
    source_article_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)  # list[int] article ids
    model_used: Mapped[str] = mapped_column(String(50), nullable=False)
    token_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    articles: Mapped[list["NewsArticle"]] = relationship(back_populates="summary")
    delivery_logs: Mapped[list["DeliveryLog"]] = relationship(
        back_populates="summary", cascade="all, delete-orphan"
    )


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    last_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="America/Sao_Paulo")
    subscribed_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    interactions: Mapped[list["UserInteraction"]] = relationship(back_populates="subscriber")
    delivery_logs: Mapped[list["DeliveryLog"]] = relationship(back_populates="subscriber")


class UserInteraction(Base):
    __tablename__ = "user_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscriber_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscribers.id"), nullable=False
    )
    incoming_message: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    command: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    response_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    subscriber: Mapped["Subscriber"] = relationship(back_populates="interactions")


class DeliveryLog(Base):
    __tablename__ = "delivery_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscriber_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("subscribers.id"), nullable=False
    )
    summary_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("summaries.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    subscriber: Mapped["Subscriber"] = relationship(back_populates="delivery_logs")
    summary: Mapped["Summary"] = relationship(back_populates="delivery_logs")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    articles_collected: Mapped[int] = mapped_column(Integer, default=0)
    summaries_generated: Mapped[int] = mapped_column(Integer, default=0)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    events: Mapped[list["PipelineEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("pipeline_runs.id"), nullable=False)
    step: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped["PipelineRun"] = relationship(back_populates="events")


VALID_CATEGORIES = [
    "politica-brasil",
    "economia-brasil",
    "economia-cripto",
    "economia-mundao",
    "politica-mundao",
    "tech",
]
