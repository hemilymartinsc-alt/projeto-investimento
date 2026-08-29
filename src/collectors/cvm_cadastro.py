from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import Any

import pandas as pd
import requests

from src.config import CVM_CAD_CIA_URL


SOURCE_CODE = "CVM_CAD_CIA"
PROCESS_NAME = "coletar_cadastro_companhias_abertas"


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def _parse_date(value: Any) -> date | None:
    text = _blank_to_none(value)
    if text is None:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _source_last_modified(response: requests.Response) -> datetime | None:
    value = response.headers.get("Last-Modified")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def download_cvm_cadastro() -> tuple[pd.DataFrame, datetime | None]:
    response = requests.get(
        CVM_CAD_CIA_URL,
        timeout=60,
        headers={
            "User-Agent": "projeto-investimento/1.0 (coleta de dados publicos CVM)"
        },
    )
    response.raise_for_status()

    df = pd.read_csv(
        BytesIO(response.content),
        sep=";",
        encoding="latin-1",
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )
    df.columns = [str(col).strip().upper() for col in df.columns]

    required = {
        "CD_CVM",
        "CNPJ_CIA",
        "DENOM_SOCIAL",
        "SIT",
        "SETOR_ATIV",
    }
    missing = sorted(required.difference(df.columns))
    if missing:
        raise RuntimeError(
            "Estrutura do arquivo da CVM mudou. "
            f"Colunas obrigatórias ausentes: {', '.join(missing)}"
        )

    return df, _source_last_modified(response)


def transform_cvm_cadastro(
    df: pd.DataFrame,
    source_updated_at: datetime | None,
) -> list[tuple]:
    rows: list[tuple] = []

    def get(row: pd.Series, column: str) -> str | None:
        if column not in row.index:
            return None
        return _blank_to_none(row[column])

    for _, row in df.iterrows():
        codigo_cvm = get(row, "CD_CVM")
        if not codigo_cvm:
            continue

        rows.append(
            (
                codigo_cvm,
                get(row, "CNPJ_CIA"),
                get(row, "DENOM_SOCIAL"),
                get(row, "DENOM_COMERC"),
                get(row, "SIT"),
                get(row, "SIT_EMISSOR"),
                get(row, "SETOR_ATIV"),
                get(row, "TP_MERC"),
                get(row, "CATEG_REG"),
                get(row, "MUNICIPIO"),
                get(row, "UF"),
                get(row, "PAIS"),
                get(row, "EMAIL"),
                _parse_date(get(row, "DT_REG")),
                _parse_date(get(row, "DT_CONST")),
                _parse_date(get(row, "DT_CANCEL")),
                SOURCE_CODE,
                source_updated_at,
            )
        )

    return rows


UPSERT_SQL = """
insert into investimento.cvm_cadastro_companhias (
    codigo_cvm,
    cnpj,
    denominacao_social,
    denominacao_comercial,
    situacao_registro,
    situacao_emissor,
    setor_atividade,
    tipo_mercado,
    categoria_registro,
    municipio,
    uf,
    pais,
    email,
    data_registro,
    data_constituicao,
    data_cancelamento,
    fonte,
    atualizado_fonte_em
)
values (
    %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s
)
on conflict (codigo_cvm) do update set
    cnpj = excluded.cnpj,
    denominacao_social = excluded.denominacao_social,
    denominacao_comercial = excluded.denominacao_comercial,
    situacao_registro = excluded.situacao_registro,
    situacao_emissor = excluded.situacao_emissor,
    setor_atividade = excluded.setor_atividade,
    tipo_mercado = excluded.tipo_mercado,
    categoria_registro = excluded.categoria_registro,
    municipio = excluded.municipio,
    uf = excluded.uf,
    pais = excluded.pais,
    email = excluded.email,
    data_registro = excluded.data_registro,
    data_constituicao = excluded.data_constituicao,
    data_cancelamento = excluded.data_cancelamento,
    fonte = excluded.fonte,
    atualizado_fonte_em = excluded.atualizado_fonte_em,
    coletado_em = now()
"""


def upsert_cvm_cadastro(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    return len(rows)
