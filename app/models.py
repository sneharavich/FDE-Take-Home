"""Database models for persistence."""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, String, Integer, DateTime, Text, UniqueConstraint, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


class Run(Base):
    """Represents a run of the alert processing."""
    __tablename__ = "runs"
    
    run_id = Column(String, primary_key=True)
    source_uri = Column(String, nullable=False)
    month = Column(String, nullable=False)  # YYYY-MM-01
    dry_run = Column(Integer, nullable=False, default=0)  # SQLite doesn't have boolean
    status = Column(String, nullable=False)  # succeeded, failed
    rows_scanned = Column(Integer, default=0)
    duplicates_found = Column(Integer, default=0)
    alerts_computed = Column(Integer, default=0)
    alerts_sent = Column(Integer, default=0)
    skipped_replay = Column(Integer, default=0)
    failed_deliveries = Column(Integer, default=0)
    unknown_region_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class AlertOutcome(Base):
    """Represents the outcome of an alert being sent."""
    __tablename__ = "alert_outcomes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, nullable=False)
    account_id = Column(String, nullable=False)
    account_name = Column(String, nullable=False)
    month = Column(String, nullable=False)  # YYYY-MM-01
    alert_type = Column(String, nullable=False, default="at_risk")
    channel = Column(String, nullable=True)
    status = Column(String, nullable=False)  # sent, skipped_replay, failed, unknown_region
    error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    
    __table_args__ = (
        UniqueConstraint('account_id', 'month', 'alert_type', name='uix_account_month_alert'),
    )


def init_db(database_url: str):
    """Initialize the database and create tables."""
    # For SQLite, add connect_args to handle concurrent access
    if database_url.startswith('sqlite'):
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            pool_recycle=3600
        )
    else:
        engine = create_engine(database_url)
    
    Base.metadata.create_all(engine)
    return engine


def get_session(engine):
    """Get a database session."""
    Session = sessionmaker(bind=engine)
    return Session()
