import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager

from app.config import Config
from app.models import init_db, get_session
from app.alert_service import AlertService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

engine = init_db(Config.DATABASE_URL)


def get_previous_month() -> str:
    """
    Calculate the first day of the previous month.
    
    Returns:
        String in format YYYY-MM-01
    """
    today = datetime.now()
    first_of_current_month = today.replace(day=1)
    last_of_previous_month = first_of_current_month - timedelta(days=1)
    first_of_previous_month = last_of_previous_month.replace(day=1)
    return first_of_previous_month.strftime('%Y-%m-%d')


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    logging.info("Starting Risk Alert Service")
    logging.info(f"Database: {Config.DATABASE_URL}")
    logging.info(f"ARR Threshold: ${Config.ARR_THRESHOLD:,}")
    yield
    logging.info("Shutting down Risk Alert Service")

app = FastAPI(title="Risk Alert Service", lifespan=lifespan)


class RunRequest(BaseModel):
    source_uri: str
    month: str  # YYYY-MM-01 or "auto" for previous month
    dry_run: bool = False


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"ok": True}


@app.post("/runs")
def create_run(req: RunRequest):
    """
    Create and execute a run synchronously.
    
    This endpoint processes the run, computes alerts, and sends them to Slack
    (unless dry_run is True). The request blocks until processing is complete.
    
    The month parameter can be:
    - "auto" - automatically uses the previous month
    - "YYYY-MM-01" - specific month to process
    """
    # Handle "auto" month
    if req.month.lower() == "auto":
        month = get_previous_month()
        logging.info(f"Auto-detected previous month: {month}")
    else:
        month = req.month
    
    db_session = get_session(engine)
    try:
        service = AlertService(db_session, Config)
        result = service.process_run(req.source_uri, month, req.dry_run)
        return result
    finally:
        db_session.close()


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    """Get the status and results of a run."""
    db_session = get_session(engine)
    try:
        service = AlertService(db_session, Config)
        result = service.get_run_status(run_id)
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        
        return result
    finally:
        db_session.close()


@app.post("/preview")
def preview(req: RunRequest):
    """
    Preview alerts without sending to Slack.
    
    Computes which alerts would be sent for the given month,
    but does not actually send them or persist any outcomes.
    
    The month parameter can be:
    - "auto" - automatically uses the previous month
    - "YYYY-MM-01" - specific month to process
    """
    # Handle "auto" month
    if req.month.lower() == "auto":
        month = get_previous_month()
        logging.info(f"Auto-detected previous month: {month}")
    else:
        month = req.month
    
    db_session = get_session(engine)
    try:
        service = AlertService(db_session, Config)
        result = service.preview_alerts(req.source_uri, month)
        return result
    finally:
        db_session.close()
