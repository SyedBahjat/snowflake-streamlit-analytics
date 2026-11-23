"""Thin warehouse client: Snowflake in production, DuckDB for offline dev and CI. Same SQL runs on both."""

import os
from pathlib import Path

import pandas as pd

SCHEMA = Path(__file__).parent / "sql" / "schema.sql"


class Warehouse:
    def __init__(
        self, backend: str | None = None, duckdb_path: str = "data/warehouse.duckdb", params: dict | None = None
    ) -> None:
        self.backend = (backend or os.getenv("WAREHOUSE", "snowflake")).lower()
        if self.backend == "snowflake":
            import snowflake.connector

            # Credentials come from `params` (Streamlit secrets in the cloud) or ~/.snowflake/connections.toml
            # locally - never from code.
            source = params or {"connection_name": os.getenv("SNOWFLAKE_CONNECTION", "default")}
            self.conn = snowflake.connector.connect(
                **source, paramstyle="qmark", enable_server_session_keep_alive_auto_detection=False
            )
        elif self.backend == "duckdb":
            import duckdb

            Path(duckdb_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = duckdb.connect(duckdb_path)
        else:
            raise ValueError(f"WAREHOUSE must be 'snowflake' or 'duckdb', got {self.backend!r}")

    @property
    def is_snowflake(self) -> bool:
        return self.backend == "snowflake"

    def query(self, sql: str, params: tuple | list = ()) -> pd.DataFrame:
        cur = self.conn.cursor()  # per-call cursor: safe under Streamlit's threads
        cur.execute(sql, list(params))
        df = cur.fetch_pandas_all() if self.is_snowflake else cur.df()
        df.columns = [c.lower() for c in df.columns]  # Snowflake returns UPPERCASE identifiers
        return df

    def execute(self, sql: str, params: tuple | list = ()) -> None:
        # DuckDB temp tables/registrations are connection-local, so writes use the main connection.
        (self.conn.cursor() if self.is_snowflake else self.conn).execute(sql, list(params))

    def ensure_schema(self) -> None:
        for stmt in SCHEMA.read_text().split(";"):
            if "CREATE" in stmt.upper():
                self.execute(stmt)

    def upsert(self, table: str, df: pd.DataFrame, key: str) -> None:
        """Bulk-load df into a temp stage table, then MERGE into the target (idempotent)."""
        stage, cols = f"{table}_stage", list(df.columns)
        self.execute(f"CREATE OR REPLACE TEMPORARY TABLE {stage} AS SELECT * FROM {table} WHERE 1 = 0")
        if self.is_snowflake:
            from snowflake.connector.pandas_tools import write_pandas

            write_pandas(self.conn, df.rename(columns=str.upper), stage.upper(), use_logical_type=True)
        else:
            self.conn.register("incoming", df)
            self.execute(f"INSERT INTO {stage} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM incoming")
            self.conn.unregister("incoming")
        updates = ", ".join(f"{c} = s.{c}" for c in cols if c != key)
        self.execute(
            f"MERGE INTO {table} t USING {stage} s ON t.{key} = s.{key} "
            f"WHEN MATCHED THEN UPDATE SET {updates} "
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({', '.join('s.' + c for c in cols)})"
        )

    def close(self) -> None:
        self.conn.close()

    def describe(self) -> str:
        if not self.is_snowflake:
            return "DuckDB (local dev mode)"
        r = self.query("SELECT CURRENT_ACCOUNT() a, CURRENT_DATABASE() d, CURRENT_SCHEMA() s, CURRENT_WAREHOUSE() w")
        a, d, s, w = r.iloc[0]
        return f"Snowflake · {a} · {d}.{s} · warehouse {w}"
