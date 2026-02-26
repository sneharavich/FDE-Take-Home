"""Data processing logic for computing risk alerts."""
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import pandas as pd
import pyarrow.parquet as pq
from dateutil.relativedelta import relativedelta


class Alert:
    """Represents a risk alert for an account."""
    
    def __init__(
        self,
        account_id: str,
        account_name: str,
        account_region: Optional[str],
        month: str,
        status: str,
        duration_months: int,
        risk_start_month: str,
        renewal_date: Optional[str],
        account_owner: Optional[str],
        arr: Optional[int],
    ):
        self.account_id = account_id
        self.account_name = account_name
        self.account_region = account_region
        self.month = month
        self.status = status
        self.duration_months = duration_months
        self.risk_start_month = risk_start_month
        self.renewal_date = renewal_date
        self.account_owner = account_owner
        self.arr = arr
    
    def to_dict(self) -> Dict:
        """Convert alert to dictionary, handling NaN values."""
        import math
        from datetime import date
        
        def clean_value(val):
            if pd.isna(val) or (isinstance(val, float) and math.isnan(val)):
                return None
            if isinstance(val, (pd.Timestamp, datetime, date)):
                return str(val)
            return val
        
        return {
            "account_id": clean_value(self.account_id),
            "account_name": clean_value(self.account_name),
            "account_region": clean_value(self.account_region),
            "month": self.month,
            "status": clean_value(self.status),
            "duration_months": self.duration_months,
            "risk_start_month": self.risk_start_month,
            "renewal_date": clean_value(self.renewal_date),
            "account_owner": clean_value(self.account_owner),
            "arr": clean_value(self.arr),
        }


class DataProcessor:
    """Process Parquet data and compute risk alerts. Current approach is simple and works fine for files up to several GB.
    If need a more optimized approach for larger files, we can consider other options like Column Pruning and Chunked processing.
    """
    
    def __init__(self, parquet_file: pq.ParquetFile, arr_threshold: int = 10000):
        self.parquet_file = parquet_file
        self.arr_threshold = arr_threshold
    
    def compute_alerts(self, target_month: str) -> Tuple[List[Alert], Dict[str, int]]:
        """Compute risk alerts for the target month."""
        
        target_date = pd.to_datetime(target_month)
        earliest_month = target_date - relativedelta(months=24)
        
        # Simple read - works fine for files up to a few GB
        table = self.parquet_file.read()
        df = table.to_pandas()
        
        df['month'] = pd.to_datetime(df['month'])
        df = df[(df['month'] >= earliest_month) & (df['month'] <= target_date)]
        
        print(f"Loaded {len(df):,} rows")
        
        rows_scanned = len(df)
        
        # Deduplication
        df['updated_at'] = pd.to_datetime(df['updated_at'])
        df = df.sort_values('updated_at')
        duplicates_found = df.duplicated(subset=['account_id', 'month'], keep='last').sum()
        
        if duplicates_found > 0:
            print(f"Found {duplicates_found} duplicates")
        
        df = df.drop_duplicates(subset=['account_id', 'month'], keep='last')
        
        # Filter for target month + At Risk
        df_target = df[
            (df['month'] == target_date) & 
            (df['status'] == 'At Risk')
        ].copy()
        
        print(f"Found {len(df_target)} At Risk accounts")
        
        # Apply ARR threshold
        if 'arr' in df_target.columns:
            df_target['arr'] = df_target['arr'].fillna(0)
            before = len(df_target)
            df_target = df_target[df_target['arr'] >= self.arr_threshold]
            if before > len(df_target):
                print(f"Filtered {before - len(df_target)} below ARR threshold")
        
        # Compute duration
        alerts = []
        for _, row in df_target.iterrows():
            duration, risk_start = self._compute_duration(df, row['account_id'], target_date)
            
            alert = Alert(
                account_id=row['account_id'],
                account_name=row['account_name'],
                account_region=row.get('account_region'),
                month=target_month,
                status=row['status'],
                duration_months=duration,
                risk_start_month=risk_start,
                renewal_date=row.get('renewal_date'),
                account_owner=row.get('account_owner'),
                arr=row.get('arr'),
            )
            alerts.append(alert)
        
        print(f"Generated {len(alerts)} alerts")
        
        return alerts, {"rows_scanned": rows_scanned, "duplicates_found": int(duplicates_found)}
    
    def _compute_duration(self, df: pd.DataFrame, account_id: str, target_date: pd.Timestamp) -> Tuple[int, str]:
        """Walk backward to compute continuous risk duration."""
        
        account_df = df[df['account_id'] == account_id].sort_values('month', ascending=False)
        
        duration = 1
        current_month = target_date
        
        while True:
            prev_month = current_month - relativedelta(months=1)
            prev_row = account_df[account_df['month'] == prev_month]
            
            if prev_row.empty or prev_row.iloc[0]['status'] != 'At Risk':
                break
            
            duration += 1
            current_month = prev_month
        
        risk_start = target_date - relativedelta(months=duration - 1)
        return duration, risk_start.strftime('%Y-%m-%d')
