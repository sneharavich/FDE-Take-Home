"""Configuration management for the Risk Alert Service."""
import os
import json
from typing import Optional, Dict


class Config:
    """Application configuration loaded from environment variables."""
    
    # Data source
    SOURCE_URI: str = os.getenv("SOURCE_URI", "file://monthly_account_status.parquet")
    
    # Slack configuration
    SLACK_WEBHOOK_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
    SLACK_WEBHOOK_BASE_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_BASE_URL")
    
    # Channel routing - default configuration
    REGION_CHANNEL_MAP: Dict = json.loads(
        os.getenv(
            "REGION_CHANNEL_MAP",
            '{"regions":{"AMER":"amer-risk-alerts","EMEA":"emea-risk-alerts","APAC":"apac-risk-alerts"}}'
        )
    )
    
    # Details URL
    DETAILS_BASE_URL: str = os.getenv("DETAILS_BASE_URL", "https://app.yourcompany.com/accounts")
    
    # Alert thresholds
    ARR_THRESHOLD: int = int(os.getenv("ARR_THRESHOLD", "10000"))
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./risk_alerts.db")
    
    # Email configuration for unknown region notifications
    SUPPORT_EMAIL: str = os.getenv("SUPPORT_EMAIL", "support@quadsci.ai")
    
    # Google Cloud credentials (for GCS access)
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    
    # Retry configuration
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BACKOFF_FACTOR: float = float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0"))
    RETRY_INITIAL_DELAY: float = float(os.getenv("RETRY_INITIAL_DELAY", "1.0"))
    
    @classmethod
    def get_channel_for_region(cls, region: Optional[str]) -> Optional[str]:
        """Get the Slack channel for a given region."""
        if not region:
            return None
        regions = cls.REGION_CHANNEL_MAP.get("regions", {})
        return regions.get(region)
    
    @classmethod
    def get_details_url(cls, account_id: str) -> str:
        """Get the details URL for an account."""
        return f"{cls.DETAILS_BASE_URL}/{account_id}"
