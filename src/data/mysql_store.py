"""
Optional MySQL sink for fundamentals.

Enable by setting MYSQL_URL, e.g.:
mysql+pymysql://user:pass@host:3306/dbname
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine


class MySQLFundamentalsStore:
    def __init__(self, mysql_url: Optional[str] = None):
        self.mysql_url = mysql_url or os.getenv("MYSQL_URL", "")
        self._engine = None
        if self.mysql_url:
            # Use SQLAlchemy with pymysql driver.
            self._engine = create_engine(
                self.mysql_url,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 10},
            )

    def enabled(self) -> bool:
        return self._engine is not None

    def write_quarterly_fundamentals(self, df: pd.DataFrame) -> None:
        if not self.enabled() or df is None or df.empty:
            return

        out = df.copy()
        # Ensure date columns are string for MySQL compatibility.
        for col in ("report_date", "disclosure_date"):
            if col in out.columns:
                out[col] = pd.to_datetime(out[col], errors="coerce").dt.date.astype(str)

        out.to_sql(
            "quarterly_fundamentals",
            self._engine,
            if_exists="append",
            index=False,
            chunksize=1000,
            method="multi",
        )
