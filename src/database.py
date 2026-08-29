from __future__ import annotations

import psycopg

from src.config import require_database_url


def connect():
    return psycopg.connect(
        require_database_url(),
        connect_timeout=20,
        application_name="projeto-investimento",
    )
