"""Service for processing and sending alerts."""
import uuid
import logging
from datetime import datetime
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.config import Config
from app.data_processor import DataProcessor, Alert
from app.models import Run, AlertOutcome
from app.slack_client import SlackClient
from app.storage import open_uri

logger = logging.getLogger(__name__)


class AlertService:
    """Service for processing risk alerts."""
    
    def __init__(self, db_session: Session, config: Config = Config):
        self.db_session = db_session
        self.config = config
        self.slack_client = SlackClient(config)
    
    def process_run(
        self, 
        source_uri: str, 
        month: str, 
        dry_run: bool = False
    ) -> Dict:
        """
        Process a run: compute alerts and send to Slack.
        
        Args:
            source_uri: URI to the Parquet file
            month: Target month in YYYY-MM-01 format
            dry_run: If True, don't send to Slack
            
        Returns:
            Dictionary with run_id and status
        """
        run_id = str(uuid.uuid4())
        
        # Create run record
        run = Run(
            run_id=run_id,
            source_uri=source_uri,
            month=month,
            dry_run=1 if dry_run else 0,
            status="running",
        )
        self.db_session.add(run)
        self.db_session.commit()
        
        try:
            # Open Parquet file
            logger.info(f"Opening source: {source_uri}")
            parquet_file = open_uri(source_uri)
            
            # Process data
            logger.info(f"Computing alerts for {month}")
            processor = DataProcessor(parquet_file, arr_threshold=self.config.ARR_THRESHOLD)
            alerts, stats = processor.compute_alerts(month)
            
            # Update run with stats
            run.rows_scanned = stats["rows_scanned"]
            run.duplicates_found = stats["duplicates_found"]
            run.alerts_computed = len(alerts)
            self.db_session.commit()
            
            logger.info(
                f"Found {len(alerts)} at-risk accounts. "
                f"Scanned {stats['rows_scanned']} rows, found {stats['duplicates_found']} duplicates."
            )
            
            # Send alerts (unless dry_run)
            if not dry_run:
                self._send_alerts(run, alerts)
            else:
                logger.info("Dry run - skipping Slack delivery")
                # In dry run, just create outcome records without sending
                for alert in alerts:
                    outcome = AlertOutcome(
                        run_id=run_id,
                        account_id=alert.account_id,
                        account_name=alert.account_name,
                        month=month,
                        alert_type="at_risk",
                        status="dry_run",
                        sent_at=datetime.utcnow(),
                    )
                    self.db_session.add(outcome)
                self.db_session.commit()
            
            # Mark run as succeeded
            run.status = "succeeded"
            run.completed_at = datetime.utcnow()
            self.db_session.commit()
            
            logger.info(f"Run {run_id} completed successfully")
            return {"run_id": run_id, "status": "succeeded"}
        
        except Exception as e:
            logger.error(f"Run {run_id} failed: {str(e)}", exc_info=True)
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.utcnow()
            self.db_session.commit()
            return {"run_id": run_id, "status": "failed", "error": str(e)}
    
    def _send_alerts(self, run: Run, alerts: List[Alert]):
        """Send alerts to Slack and persist outcomes."""
        unknown_region_alerts = []
        
        for alert in alerts:
            # Check if alert already processed (replay safety) - check first before anything
            existing = self.db_session.query(AlertOutcome).filter(
                AlertOutcome.account_id == alert.account_id,
                AlertOutcome.month == alert.month,
                AlertOutcome.alert_type == "at_risk"
            ).first()
            
            if existing:
                logger.info(f"Alert for {alert.account_id} already processed, skipping (replay safety)")
                run.skipped_replay += 1
                continue
            
            # Get channel for region
            channel = self.config.get_channel_for_region(alert.account_region)
            
            if not channel:
                # Unknown region - don't send, record as failed
                logger.warning(
                    f"Unknown region '{alert.account_region}' for account {alert.account_id}"
                )
                unknown_region_alerts.append(alert)
                
                outcome = AlertOutcome(
                    run_id=run.run_id,
                    account_id=alert.account_id,
                    account_name=alert.account_name,
                    month=alert.month,
                    alert_type="at_risk",
                    status="failed",
                    error_message="unknown_region",
                    sent_at=datetime.utcnow(),
                )
                self.db_session.add(outcome)
                run.failed_deliveries += 1
                run.unknown_region_count += 1
                continue
            
            # Send to Slack
            success, error_msg = self.slack_client.send_alert(alert, channel)
            
            if success:
                status = "sent"
                run.alerts_sent += 1
            else:
                status = "failed"
                run.failed_deliveries += 1
            
            # Persist outcome
            outcome = AlertOutcome(
                run_id=run.run_id,
                account_id=alert.account_id,
                account_name=alert.account_name,
                month=alert.month,
                alert_type="at_risk",
                channel=channel,
                status=status,
                error_message=error_msg,
                sent_at=datetime.utcnow(),
            )
            self.db_session.add(outcome)
        
        self.db_session.commit()
        
        # Send aggregated notification for unknown regions
        if unknown_region_alerts:
            self._send_unknown_region_notification(run, unknown_region_alerts)
    
    def _send_unknown_region_notification(self, run: Run, alerts: List[Alert]):
        """
        Send aggregated notification for accounts with unknown regions.
        
        This is a stub implementation that logs the notification.
        In production, this would send an actual email.
        """
        logger.warning(
            f"Would send email to {self.config.SUPPORT_EMAIL} about "
            f"{len(alerts)} accounts with unknown regions in run {run.run_id}"
        )
        
        # Format the notification
        account_list = "\n".join([
            f"  - {alert.account_id} ({alert.account_name}): region='{alert.account_region}'"
            for alert in alerts
        ])
        
        notification = f"""
To: {self.config.SUPPORT_EMAIL}
Subject: Risk Alert - Unknown Regions Detected

Run ID: {run.run_id}
Month: {run.month}

The following {len(alerts)} accounts have unknown or null regions and could not be routed to Slack:

{account_list}

Please update the account regions or the REGION_CHANNEL_MAP configuration.
"""
        logger.info(f"Unknown region notification:\n{notification}")
    
    def get_run_status(self, run_id: str) -> Optional[Dict]:
        """Get the status of a run."""
        run = self.db_session.query(Run).filter(Run.run_id == run_id).first()
        
        if not run:
            return None
        
        # Get alert outcomes
        outcomes = self.db_session.query(AlertOutcome).filter(
            AlertOutcome.run_id == run_id
        ).all()
        
        # Get sample alerts (first 5)
        sample_alerts = []
        sample_errors = []
        
        for outcome in outcomes[:5]:
            if outcome.status == "sent":
                sample_alerts.append({
                    "account_id": outcome.account_id,
                    "account_name": outcome.account_name,
                    "channel": outcome.channel,
                    "status": outcome.status,
                })
            elif outcome.status == "failed":
                sample_errors.append({
                    "account_id": outcome.account_id,
                    "account_name": outcome.account_name,
                    "error": outcome.error_message,
                })
        
        return {
            "run_id": run.run_id,
            "source_uri": run.source_uri,
            "month": run.month,
            "dry_run": bool(run.dry_run),
            "status": run.status,
            "counts": {
                "rows_scanned": run.rows_scanned,
                "duplicates_found": run.duplicates_found,
                "alerts_computed": run.alerts_computed,
                "alerts_sent": run.alerts_sent,
                "skipped_replay": run.skipped_replay,
                "failed_deliveries": run.failed_deliveries,
                "unknown_region_count": run.unknown_region_count,
            },
            "sample_alerts": sample_alerts,
            "sample_errors": sample_errors,
            "error_message": run.error_message,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }
    
    def preview_alerts(self, source_uri: str, month: str) -> Dict:
        """
        Preview alerts without sending to Slack.
        
        Args:
            source_uri: URI to the Parquet file
            month: Target month in YYYY-MM-01 format
            
        Returns:
            Dictionary with alerts and stats
        """
        try:
            # Open Parquet file
            parquet_file = open_uri(source_uri)
            
            # Process data
            processor = DataProcessor(parquet_file, arr_threshold=self.config.ARR_THRESHOLD)
            alerts, stats = processor.compute_alerts(month)
            
            # Convert alerts to dicts
            alert_dicts = [alert.to_dict() for alert in alerts]
            
            return {
                "month": month,
                "alerts": alert_dicts,
                "stats": stats,
            }
        
        except Exception as e:
            logger.error(f"Preview failed: {str(e)}", exc_info=True)
            return {
                "month": month,
                "error": str(e),
                "alerts": [],
                "stats": {},
            }
