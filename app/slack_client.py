"""Slack client for sending alerts with retry logic."""
import time
import logging
from typing import Optional, Tuple
import requests
from app.config import Config
from app.data_processor import Alert

logger = logging.getLogger(__name__)


class SlackClient:
    """Client for sending Slack messages with retry logic."""
    
    def __init__(self, config: Config = Config):
        self.config = config
        self.max_retries = config.MAX_RETRIES
        self.backoff_factor = config.RETRY_BACKOFF_FACTOR
        self.initial_delay = config.RETRY_INITIAL_DELAY
    
    def send_alert(self, alert: Alert, channel: str) -> Tuple[bool, Optional[str]]:
        """
        Send an alert to Slack.
        
        Args:
            alert: Alert to send
            channel: Slack channel name
            
        Returns:
            Tuple of (success, error_message)
        """
        # Build the webhook URL
        if self.config.SLACK_WEBHOOK_BASE_URL:
            # Base URL mode (for mock server or channel-specific webhooks)
            webhook_url = f"{self.config.SLACK_WEBHOOK_BASE_URL}/{channel}"
        elif self.config.SLACK_WEBHOOK_URL:
            # Single webhook mode
            webhook_url = self.config.SLACK_WEBHOOK_URL
        else:
            return False, "No Slack webhook configured"
        
        # Format the message
        message = self._format_alert_message(alert)
        
        # Send with retry logic
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    webhook_url,
                    json=message,
                    timeout=10
                )
                
                if response.status_code == 200:
                    logger.info(f"Alert sent successfully for {alert.account_id} to {channel}")
                    return True, None
                
                elif response.status_code == 429 or response.status_code >= 500:
                    # Retriable error
                    retry_after = response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = self.initial_delay * (self.backoff_factor ** attempt)
                    else:
                        delay = self.initial_delay * (self.backoff_factor ** attempt)
                    
                    if attempt < self.max_retries:
                        logger.warning(
                            f"Slack returned {response.status_code}, retrying in {delay}s "
                            f"(attempt {attempt + 1}/{self.max_retries})"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        error_msg = f"Max retries exceeded. Last status: {response.status_code}"
                        logger.error(error_msg)
                        return False, error_msg
                
                else:
                    # Non-retriable error
                    error_msg = f"Slack returned {response.status_code}: {response.text}"
                    logger.error(error_msg)
                    return False, error_msg
            
            except requests.exceptions.RequestException as e:
                error_msg = f"Request failed: {str(e)}"
                if attempt < self.max_retries:
                    delay = self.initial_delay * (self.backoff_factor ** attempt)
                    logger.warning(f"{error_msg}, retrying in {delay}s")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"{error_msg}, max retries exceeded")
                    return False, error_msg
        
        return False, "Unknown error"
    
    def _format_alert_message(self, alert: Alert) -> dict:
        """Format an alert as a Slack message."""
        # Format renewal date
        renewal_str = alert.renewal_date if alert.renewal_date else "Unknown"
        
        # Format ARR
        arr_str = f"${alert.arr:,}" if alert.arr is not None else "Unknown"
        
        # Format owner
        owner_str = alert.account_owner if alert.account_owner else "Unassigned"
        
        # Format region
        region_str = alert.account_region if alert.account_region else "Unknown"
        
        # Build details URL
        details_url = self.config.get_details_url(alert.account_id)
        
        # Build message text
        text = (
            f"🚩 *At Risk: {alert.account_name} ({alert.account_id})*\n"
            f"• *Region:* {region_str}\n"
            f"• *At Risk for:* {alert.duration_months} month{'s' if alert.duration_months != 1 else ''} "
            f"(since {alert.risk_start_month})\n"
            f"• *ARR:* {arr_str}\n"
            f"• *Renewal Date:* {renewal_str}\n"
            f"• *Owner:* {owner_str}\n"
            f"• *Details:* {details_url}"
        )
        
        return {"text": text}
