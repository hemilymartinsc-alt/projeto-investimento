from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
CVM_CAD_CIA_URL = os.getenv(
    "CVM_CAD_CIA_URL",
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv",
).strip()


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL não configurada. "
            "Use .env localmente ou GitHub Actions Secrets."
        )
    return DATABASE_URL
