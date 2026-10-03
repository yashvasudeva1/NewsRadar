from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("external_id", name="uq_article_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    author: Mapped[str] = mapped_column(String(300), default="")
    image_url: Mapped[str] = mapped_column(String(2000), default="")
    language: Mapped[str] = mapped_column(String(20), default="en")
    categories: Mapped[str] = mapped_column(Text, default="[]")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_domain: Mapped[str] = mapped_column(String(300), default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    notifications: Mapped[list["Notification"]] = relationship(back_populates="article")


class ResearchItem(Base):
    """Scholarly paper or research-news item."""

    __tablename__ = "research_items"
    __table_args__ = (UniqueConstraint("external_id", name="uq_research_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # paper | news
    title: Mapped[str] = mapped_column(String(700), nullable=False)
    abstract: Mapped[str] = mapped_column(Text, default="")
    authors: Mapped[str] = mapped_column(Text, default="[]")
    categories: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(200), default="")
    source_domain: Mapped[str] = mapped_column(String(300), default="")
    venue: Mapped[str] = mapped_column(String(300), default="")
    doi: Mapped[str] = mapped_column(String(300), default="")
    paper_id: Mapped[str] = mapped_column(String(200), default="")
    landing_url: Mapped[str] = mapped_column(String(2000), default="")
    pdf_url: Mapped[str] = mapped_column(String(2000), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    matched_keywords: Mapped[str] = mapped_column(Text, default="[]")
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    notifications: Mapped[list["ResearchNotification"]] = relationship(back_populates="research_item")


class Interest(Base):
    __tablename__ = "interests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("article_id", name="uq_notification_article"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    article: Mapped[Article] = relationship(back_populates="notifications")


class ResearchNotification(Base):
    __tablename__ = "research_notifications"
    __table_args__ = (UniqueConstraint("research_item_id", name="uq_research_notification_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_item_id: Mapped[int] = mapped_column(ForeignKey("research_items.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    research_item: Mapped[ResearchItem] = relationship(back_populates="notifications")


class SystemState(Base):
    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
