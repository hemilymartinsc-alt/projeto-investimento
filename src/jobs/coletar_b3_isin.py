from __future__ import annotations

import base64
import csv
import io
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from zipfile import BadZipFile, ZipFile

import requests

from src.database import connect


SOURCE_CODE = "B3_ISIN"
PROCESS_NAME = "coletar_cadastro_isin_b3"

TEXT_DOWNLOAD_URL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "isinProxy/IsinCall/GetTextDownload/"
)
FILE_DOWNLOAD_URL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "isinProxy/IsinCall/GetFileDownload/{token}"
)

HTTP_TIMEOUT = (5, 60)
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF_SECONDS = 1
USER_AGENT = "projeto-investimento/1.0 (coleta de dados publicos B3)"

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
CNPJ_RE = re.compile(r"^\d{14}$")

MIN_EMITTERS = 60_000
MIN_ISIN_ROWS = 300_000
MIN_VALID_CNPJ_RATIO = 0.90
MIN_ISIN_TO_ISSUER_RATIO = 0.95
MAX_TOTAL_VARIATION_RATIO = 0.20

AUDIT_TICKERS = ("HGLG11", "KNRI11", "AFHI11", "AAGR11", "AAZQ11")


class B3IsinError(RuntimeError):
    """Falha fechada de download, layout, sanidade ou carga B3 ISIN."""


def progress(message: str) -> None:
    print(f"B3 ISIN: {message}", flush=True)


def encode_payload(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def normalize_cnpj(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits if CNPJ_RE.fullmatch(digits) else None


def parse_reference_date(value: str) -> datetime.date:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except ValueError as exc:
        raise B3IsinError(f"data de referência B3 inválida: {value!r}") from exc


def request(session: requests.Session, url: str) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            response = session.get(
                url,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                    "Referer": "https://sistemaswebb3-listados.b3.com.br/",
                },
            )
            response.raise_for_status()
            if not response.content:
                raise B3IsinError(f"resposta vazia: {url}")
            return response
        except (requests.RequestException, B3IsinError) as exc:
            last_error = exc
            if attempt == DOWNLOAD_ATTEMPTS:
                break
            time.sleep(DOWNLOAD_BACKOFF_SECONDS * attempt)
    raise B3IsinError(
        f"falha após {DOWNLOAD_ATTEMPTS} tentativas: {url} | {last_error}"
    ) from last_error


def download_official_zip() -> tuple[bytes, datetime.date]:
    with requests.Session() as session:
        metadata = request(session, TEXT_DOWNLOAD_URL).json()
        geral_pt = metadata.get("geralPt")
        if not isinstance(geral_pt, dict) or not geral_pt.get("id"):
            raise B3IsinError("layout GetTextDownload mudou: geralPt.id ausente")

        data_geracao = str(geral_pt.get("dataGeracao") or "")
        if len(data_geracao) < 10:
            raise B3IsinError("dataGeracao ausente no metadata oficial B3")
        try:
            reference_date = datetime.fromisoformat(
                data_geracao[:10]
            ).date()
        except ValueError as exc:
            raise B3IsinError(
                f"dataGeracao inválida: {data_geracao!r}"
            ) from exc

        token = encode_payload(geral_pt["id"])
        payload = request(
            session, FILE_DOWNLOAD_URL.format(token=token)
        ).content

    if not payload.startswith(b"PK\x03\x04"):
        raise B3IsinError("Banco de Dados Completo ISIN não retornou ZIP")
    return payload, reference_date


def read_rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig", errors="strict")
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=","))


def parse_emissores(content: bytes, reference_date):
    rows = read_rows(content)
    if len(rows) < MIN_EMITTERS:
        raise B3IsinError(
            f"EMISSOR.TXT abaixo do mínimo: {len(rows)} < {MIN_EMITTERS}"
        )

    field_counts = Counter(len(row) for row in rows)
    if field_counts.get(4, 0) / len(rows) < 0.99:
        raise B3IsinError(
            f"layout EMISSOR.TXT inesperado: field_counts={dict(field_counts)}"
        )

    parsed = []
    unique_codes = set()
    valid_cnpj = 0

    for row in rows:
        if len(row) != 4:
            continue
        code = row[0].strip()
        name = row[1].strip()
        cnpj = normalize_cnpj(row[2])
        if not code or not name:
            raise B3IsinError("emissor sem código ou nome")
        if code in unique_codes:
            raise B3IsinError(f"código de emissor duplicado: {code}")
        unique_codes.add(code)
        if cnpj:
            valid_cnpj += 1
        parsed.append(
            {
                "codigo_emissor_b3": code,
                "nome_emissor": name,
                "cnpj_emissor_normalizado": cnpj,
                "campo_4_oficial": row[3].strip() or None,
                "data_referencia": reference_date,
                "dados_raw": row,
            }
        )

    ratio = valid_cnpj / len(parsed)
    if ratio < MIN_VALID_CNPJ_RATIO:
        raise B3IsinError(
            f"cobertura CNPJ abaixo do mínimo: {ratio:.4%}"
        )
    return parsed, {
        "rows": len(rows),
        "unique_codes": len(unique_codes),
        "valid_cnpj": valid_cnpj,
        "valid_cnpj_ratio": ratio,
        "field_counts": dict(field_counts),
    }


def parse_isins(content: bytes, emitters_by_code: dict[str, dict], reference_date):
    rows = read_rows(content)
    if len(rows) < MIN_ISIN_ROWS:
        raise B3IsinError(
            f"NUMERACA.TXT abaixo do mínimo: {len(rows)} < {MIN_ISIN_ROWS}"
        )

    field_counts = Counter(len(row) for row in rows)
    dominant_fields, dominant_count = field_counts.most_common(1)[0]
    if dominant_count / len(rows) < 0.99 or dominant_fields < 40:
        raise B3IsinError(
            f"layout NUMERACA.TXT inesperado: field_counts={dict(field_counts)}"
        )

    parsed = []
    unique_isins = set()
    issuer_matches = 0

    for row in rows:
        if len(row) < 6:
            continue

        # Estrutura oficial observada no Banco de Dados Completo ISIN:
        # 0 data de referência, 1 situação, 2 ISIN, 3 código do emissor,
        # 4 CFI, 5 descrição. O restante é preservado integralmente no raw.
        isin = row[2].strip().upper()
        if not ISIN_RE.fullmatch(isin):
            raise B3IsinError(f"ISIN inválido na posição oficial 2: {isin!r}")

        if isin in unique_isins:
            raise B3IsinError(f"ISIN duplicado no snapshot oficial: {isin}")
        unique_isins.add(isin)

        row_reference = parse_reference_date(row[0])
        if row_reference != reference_date:
            raise B3IsinError(
                "data de referência divergente entre metadata e NUMERACA.TXT: "
                f"{row_reference} != {reference_date}"
            )

        issuer_code = row[3].strip()
        if issuer_code in emitters_by_code:
            issuer_matches += 1

        parsed.append(
            {
                "isin": isin,
                "codigo_emissor_b3": issuer_code,
                "data_referencia": row_reference,
                "situacao_b3": row[1].strip() or None,
                "cfi_code": row[4].strip() or None,
                "descricao_instrumento": row[5].strip() or None,
                "moeda": row[11].strip() if len(row) > 11 and row[11].strip() else None,
                "origem_b3": row[-1].strip() if row and row[-1].strip() else None,
                "dados_raw": row,
            }
        )

    match_ratio = issuer_matches / len(parsed)
    if match_ratio < MIN_ISIN_TO_ISSUER_RATIO:
        raise B3IsinError(
            f"vínculo ISIN->emissor abaixo do mínimo: {match_ratio:.4%}"
        )

    return parsed, {
        "rows": len(rows),
        "unique_isin": len(unique_isins),
        "issuer_matches": issuer_matches,
        "issuer_match_ratio": match_ratio,
        "field_counts": dict(field_counts),
    }


def extract_official_payloads(zip_bytes: bytes):
    try:
        with ZipFile(io.BytesIO(zip_bytes)) as archive:
            bad = archive.testzip()
            if bad:
                raise B3IsinError(f"ZIP oficial corrompido: {bad}")
            names = set(archive.namelist())
            required = {"EMISSOR.TXT", "NUMERACA.TXT"}
            missing = required - names
            if missing:
                raise B3IsinError(
                    f"arquivos obrigatórios ausentes: {sorted(missing)}"
                )
            return archive.read("EMISSOR.TXT"), archive.read("NUMERACA.TXT")
    except BadZipFile as exc:
        raise B3IsinError("ZIP oficial inválido") from exc


def baseline_counts(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                (select count(*) from investimento.b3_isin_emissores),
                (select count(*) from investimento.b3_isin_cadastro)
            """
        )
        emitters, isins = cur.fetchone()
    return int(emitters), int(isins)


def check_variation(current: int, previous: int, label: str):
    if previous <= 0:
        return
    ratio = abs(current - previous) / previous
    if ratio > MAX_TOTAL_VARIATION_RATIO:
        raise B3IsinError(
            f"variação anormal em {label}: anterior={previous} "
            f"atual={current} variacao={ratio:.2%}"
        )


def copy_rows(cur, table: str, columns: tuple[str, ...], rows: list[dict]):
    sql = (
        f"COPY {table} ({', '.join(columns)}) "
        "FROM STDIN"
    )
    with cur.copy(sql) as copy:
        for row in rows:
            values = []
            for column in columns:
                value = row[column]
                if column == "dados_raw":
                    value = json.dumps(value, ensure_ascii=False)
                values.append(value)
            copy.write_row(values)


def replace_snapshot(conn, emitters, isins):
    emitter_columns = (
        "codigo_emissor_b3",
        "nome_emissor",
        "cnpj_emissor_normalizado",
        "campo_4_oficial",
        "data_referencia",
        "dados_raw",
    )
    isin_columns = (
        "isin",
        "codigo_emissor_b3",
        "data_referencia",
        "situacao_b3",
        "cfi_code",
        "descricao_instrumento",
        "moeda",
        "origem_b3",
        "dados_raw",
    )

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                create temp table tmp_b3_isin_emissores
                (like investimento.b3_isin_emissores including defaults)
                on commit drop
                """
            )
            cur.execute(
                """
                create temp table tmp_b3_isin_cadastro
                (like investimento.b3_isin_cadastro including defaults)
                on commit drop
                """
            )
            copy_rows(
                cur, "tmp_b3_isin_emissores", emitter_columns, emitters
            )
            copy_rows(cur, "tmp_b3_isin_cadastro", isin_columns, isins)

            cur.execute("truncate investimento.b3_isin_cadastro")
            cur.execute("truncate investimento.b3_isin_emissores")
            cur.execute(
                """
                insert into investimento.b3_isin_emissores (
                    codigo_emissor_b3, nome_emissor,
                    cnpj_emissor_normalizado, campo_4_oficial,
                    data_referencia, dados_raw
                )
                select
                    codigo_emissor_b3, nome_emissor,
                    cnpj_emissor_normalizado, campo_4_oficial,
                    data_referencia, dados_raw
                from tmp_b3_isin_emissores
                """
            )
            cur.execute(
                """
                insert into investimento.b3_isin_cadastro (
                    isin, codigo_emissor_b3, data_referencia,
                    situacao_b3, cfi_code, descricao_instrumento,
                    moeda, origem_b3, dados_raw
                )
                select
                    isin, codigo_emissor_b3, data_referencia,
                    situacao_b3, cfi_code, descricao_instrumento,
                    moeda, origem_b3, dados_raw
                from tmp_b3_isin_cadastro
                """
            )


def audit_funds(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*) as fundos_b3,
                count(i.isin) as isin_encontrado,
                count(e.cnpj_emissor_normalizado) as cnpj_oficial_b3
            from investimento.ativos a
            left join investimento.b3_isin_cadastro i on i.isin = a.isin
            left join investimento.b3_isin_emissores e
                on e.codigo_emissor_b3 = i.codigo_emissor_b3
            where a.classe = 'FUNDO'
              and a.instrumento_canonico = true
              and a.atividade_confirmada_b3 = true
            """
        )
        totals = cur.fetchone()

        cur.execute(
            """
            select
                a.ticker,
                a.isin,
                i.codigo_emissor_b3,
                e.nome_emissor,
                e.cnpj_emissor_normalizado is not null as possui_cnpj_b3
            from investimento.ativos a
            left join investimento.b3_isin_cadastro i on i.isin = a.isin
            left join investimento.b3_isin_emissores e
                on e.codigo_emissor_b3 = i.codigo_emissor_b3
            where a.ticker = any(%s)
            order by a.ticker
            """,
            (list(AUDIT_TICKERS),),
        )
        examples = [
            {
                "ticker": row[0],
                "isin": row[1],
                "codigo_emissor_b3": row[2],
                "nome_emissor_b3": row[3],
                "possui_cnpj_b3": row[4],
            }
            for row in cur.fetchall()
        ]
    return {
        "fundos_b3": totals[0],
        "isin_encontrado": totals[1],
        "cnpj_oficial_b3": totals[2],
        "exemplos": examples,
    }


def create_log(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo, processo, status, iniciado_em
            )
            values (%s, %s, 'EM_EXECUCAO', now())
            returning id
            """,
            (SOURCE_CODE, PROCESS_NAME),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def finish_log(conn, log_id, status, read_count=0, written_count=0, message=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            update investimento.coletas_log
            set status = %s,
                finalizado_em = now(),
                registros_lidos = %s,
                registros_gravados = %s,
                mensagem = %s
            where id = %s
            """,
            (
                status,
                read_count,
                written_count,
                json.dumps(message, ensure_ascii=False) if message is not None else None,
                log_id,
            ),
        )
    conn.commit()


def main():
    conn = connect()
    log_id = create_log(conn)
    read_count = 0
    try:
        progress("baixando Banco de Dados Completo ISIN oficial")
        zip_bytes, reference_date = download_official_zip()
        emissor_bytes, numeraca_bytes = extract_official_payloads(zip_bytes)

        emitters, emitter_metrics = parse_emissores(
            emissor_bytes, reference_date
        )
        emitters_by_code = {
            row["codigo_emissor_b3"]: row for row in emitters
        }
        isins, isin_metrics = parse_isins(
            numeraca_bytes, emitters_by_code, reference_date
        )
        read_count = len(emitters) + len(isins)

        previous_emitters, previous_isins = baseline_counts(conn)
        check_variation(len(emitters), previous_emitters, "emissores")
        check_variation(len(isins), previous_isins, "ISINs")

        progress(
            "sanidade aprovada | "
            f"emissores={len(emitters)} isins={len(isins)} "
            f"ref={reference_date}"
        )

        replace_snapshot(conn, emitters, isins)
        audit = audit_funds(conn)

        metrics = {
            "data_referencia": str(reference_date),
            "emissores": emitter_metrics,
            "isins": isin_metrics,
            "auditoria_fundos": audit,
        }
        finish_log(
            conn,
            log_id,
            "SUCESSO",
            read_count=read_count,
            written_count=read_count,
            message=metrics,
        )
        progress("SUCESSO | " + json.dumps(metrics, ensure_ascii=False))
    except Exception as exc:
        try:
            finish_log(
                conn,
                log_id,
                "ERRO",
                read_count=read_count,
                written_count=0,
                message={"erro": str(exc)},
            )
        finally:
            conn.close()
        raise
    else:
        conn.close()


if __name__ == "__main__":
    main()
