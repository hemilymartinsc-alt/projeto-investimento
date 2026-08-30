from __future__ import annotations

import base64
import csv
import io
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime
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

AUDIT_TARGETS = {
    "AAGR11": "BRAAGRCTF017",
    "AAZQ11": "BRAAZQCTF001",
    "AFHI11": "BRAFHICTF005",
    "HGLG11": "BRHGLGCTF004",
    "KNRI11": "BRKNRICTF007",
}


class B3IsinError(RuntimeError):
    """Falha fechada de download, layout, sanidade ou carga B3 ISIN."""


def progress(message: str) -> None:
    print(f"B3 ISIN: {message}", flush=True)


def encode_payload(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.b64encode(raw).decode("ascii")


def normalize_cnpj(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits if CNPJ_RE.fullmatch(digits) else None


def mask_cnpj(value: str | None) -> str | None:
    return f"***{value[-4:]}" if value else None


def parse_reference_date(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date()
    except ValueError as exc:
        raise B3IsinError(
            f"data de referência B3 inválida: {value!r}"
        ) from exc


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
                    "Referer": (
                        "https://sistemaswebb3-listados.b3.com.br/"
                    ),
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
        f"falha após {DOWNLOAD_ATTEMPTS} tentativas: "
        f"{url} | {last_error}"
    ) from last_error


def download_official_zip() -> tuple[bytes, date]:
    with requests.Session() as session:
        metadata_response = request(session, TEXT_DOWNLOAD_URL)
        try:
            metadata = metadata_response.json()
        except ValueError as exc:
            raise B3IsinError(
                "GetTextDownload não retornou JSON"
            ) from exc

        geral_pt = metadata.get("geralPt")
        if not isinstance(geral_pt, dict) or not geral_pt.get("id"):
            raise B3IsinError(
                "layout GetTextDownload mudou: geralPt.id ausente"
            )

        data_geracao = str(geral_pt.get("dataGeracao") or "")
        if len(data_geracao) < 10:
            raise B3IsinError(
                "dataGeracao ausente no metadata oficial B3"
            )
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
        raise B3IsinError(
            "Banco de Dados Completo ISIN não retornou ZIP"
        )
    return payload, reference_date


def read_rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8-sig", errors="strict")
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=","))


def extract_official_payloads(zip_bytes: bytes):
    try:
        with ZipFile(io.BytesIO(zip_bytes)) as archive:
            bad = archive.testzip()
            if bad:
                raise B3IsinError(
                    f"ZIP oficial corrompido: {bad}"
                )
            names = set(archive.namelist())
            required = {"EMISSOR.TXT", "NUMERACA.TXT"}
            missing = required - names
            if missing:
                raise B3IsinError(
                    f"arquivos obrigatórios ausentes: "
                    f"{sorted(missing)}"
                )
            return (
                archive.read("EMISSOR.TXT"),
                archive.read("NUMERACA.TXT"),
            )
    except BadZipFile as exc:
        raise B3IsinError("ZIP oficial inválido") from exc


def parse_emissores(content: bytes, reference_date: date):
    rows = read_rows(content)
    if len(rows) < MIN_EMITTERS:
        raise B3IsinError(
            f"EMISSOR.TXT abaixo do mínimo: "
            f"{len(rows)} < {MIN_EMITTERS}"
        )

    field_counts = Counter(len(row) for row in rows)
    if field_counts.get(4, 0) / len(rows) < 0.99:
        raise B3IsinError(
            "layout EMISSOR.TXT inesperado: "
            f"field_counts={dict(field_counts)}"
        )

    parsed = []
    by_code: dict[str, list[dict]] = defaultdict(list)
    valid_cnpj = 0

    for line_number, row in enumerate(rows, start=1):
        if len(row) != 4:
            continue
        code = row[0].strip()
        name = row[1].strip()
        cnpj = normalize_cnpj(row[2])
        if not code or not name:
            raise B3IsinError(
                f"emissor sem código/nome na linha {line_number}"
            )
        if cnpj:
            valid_cnpj += 1

        item = {
            "codigo_emissor_b3": code,
            "nome_emissor": name,
            "cnpj_emissor_normalizado": cnpj,
            "campo_4_oficial": row[3].strip() or None,
            "linha_arquivo": line_number,
            "data_referencia": reference_date,
            "dados_raw": row,
        }
        parsed.append(item)
        by_code[code].append(item)

    cnpj_ratio = valid_cnpj / len(parsed)
    if cnpj_ratio < MIN_VALID_CNPJ_RATIO:
        raise B3IsinError(
            f"cobertura CNPJ abaixo do mínimo: {cnpj_ratio:.4%}"
        )

    duplicated = {
        code: items for code, items in by_code.items()
        if len(items) > 1
    }
    cnpj_conflicts = {
        code: items for code, items in duplicated.items()
        if len({
            item["cnpj_emissor_normalizado"]
            for item in items
            if item["cnpj_emissor_normalizado"]
        }) > 1
    }
    name_conflicts = {
        code: items for code, items in duplicated.items()
        if len({item["nome_emissor"] for item in items}) > 1
    }

    metrics = {
        "rows": len(rows),
        "parsed_rows": len(parsed),
        "unique_codes": len(by_code),
        "duplicate_codes": len(duplicated),
        "extra_duplicate_rows": sum(
            len(items) - 1 for items in duplicated.values()
        ),
        "codes_with_cnpj_conflict": len(cnpj_conflicts),
        "codes_with_name_conflict": len(name_conflicts),
        "valid_cnpj": valid_cnpj,
        "valid_cnpj_ratio": cnpj_ratio,
        "field_counts": dict(sorted(field_counts.items())),
    }
    return parsed, by_code, metrics


def parse_isins(
    content: bytes,
    emitter_codes: set[str],
    reference_date: date,
):
    rows = read_rows(content)
    if len(rows) < MIN_ISIN_ROWS:
        raise B3IsinError(
            f"NUMERACA.TXT abaixo do mínimo: "
            f"{len(rows)} < {MIN_ISIN_ROWS}"
        )

    field_counts = Counter(len(row) for row in rows)
    dominant_fields, dominant_count = field_counts.most_common(1)[0]
    if (
        dominant_count / len(rows) < 0.99
        or dominant_fields < 40
    ):
        raise B3IsinError(
            "layout NUMERACA.TXT inesperado: "
            f"field_counts={dict(field_counts)}"
        )

    parsed = []
    by_isin: dict[str, list[dict]] = defaultdict(list)
    issuer_matches = 0
    reference_dates = Counter()

    for line_number, row in enumerate(rows, start=1):
        if len(row) < 6:
            raise B3IsinError(
                f"NUMERACA.TXT linha curta: {line_number}"
            )

        isin = row[2].strip().upper()
        if not ISIN_RE.fullmatch(isin):
            raise B3IsinError(
                "ISIN inválido na posição oficial 2: "
                f"linha={line_number} valor={isin!r}"
            )

        row_reference = parse_reference_date(row[0])
        reference_dates[row_reference] += 1
        if row_reference != reference_date:
            raise B3IsinError(
                "data de referência divergente entre metadata "
                f"e NUMERACA.TXT: linha={line_number} "
                f"{row_reference} != {reference_date}"
            )

        issuer_code = row[3].strip()
        if issuer_code in emitter_codes:
            issuer_matches += 1

        item = {
            "isin": isin,
            "codigo_emissor_b3": issuer_code,
            "linha_arquivo": line_number,
            "data_referencia": row_reference,
            "situacao_b3": row[1].strip() or None,
            "cfi_code": row[4].strip() or None,
            "descricao_instrumento": row[5].strip() or None,
            "moeda": (
                row[11].strip()
                if len(row) > 11 and row[11].strip()
                else None
            ),
            "origem_b3": (
                row[-1].strip()
                if row and row[-1].strip()
                else None
            ),
            "dados_raw": row,
        }
        parsed.append(item)
        by_isin[isin].append(item)

    match_ratio = issuer_matches / len(parsed)
    if match_ratio < MIN_ISIN_TO_ISSUER_RATIO:
        raise B3IsinError(
            "vínculo ISIN->emissor abaixo do mínimo: "
            f"{match_ratio:.4%}"
        )

    duplicated = {
        isin: items for isin, items in by_isin.items()
        if len(items) > 1
    }
    issuer_conflicts = {
        isin: items for isin, items in duplicated.items()
        if len({
            item["codigo_emissor_b3"] for item in items
        }) > 1
    }

    metrics = {
        "rows": len(rows),
        "parsed_rows": len(parsed),
        "unique_isin": len(by_isin),
        "duplicate_isins": len(duplicated),
        "extra_duplicate_rows": sum(
            len(items) - 1 for items in duplicated.values()
        ),
        "isins_with_issuer_conflict": len(issuer_conflicts),
        "issuer_matches": issuer_matches,
        "issuer_match_ratio": match_ratio,
        "reference_dates": {
            str(key): value
            for key, value in reference_dates.items()
        },
        "field_counts": dict(sorted(field_counts.items())),
    }
    return parsed, by_isin, metrics


def unique_bridge_for(
    isin: str,
    by_isin: dict[str, list[dict]],
    by_code: dict[str, list[dict]],
):
    isin_rows = by_isin.get(isin, [])
    issuer_codes = {
        row["codigo_emissor_b3"] for row in isin_rows
    }
    if len(issuer_codes) != 1:
        return {
            "status": "AMBIGUO_ISIN",
            "qtd_linhas_isin": len(isin_rows),
            "qtd_codigos_emissor": len(issuer_codes),
        }

    issuer_code = next(iter(issuer_codes))
    issuer_rows = by_code.get(issuer_code, [])
    cnpjs = {
        row["cnpj_emissor_normalizado"]
        for row in issuer_rows
        if row["cnpj_emissor_normalizado"]
    }
    has_null_cnpj = any(
        not row["cnpj_emissor_normalizado"]
        for row in issuer_rows
    )
    if len(cnpjs) != 1 or has_null_cnpj:
        return {
            "status": "AMBIGUO_EMISSOR",
            "codigo_emissor_b3": issuer_code,
            "qtd_linhas_emissor": len(issuer_rows),
            "qtd_cnpjs_distintos": len(cnpjs),
            "possui_cnpj_nulo": has_null_cnpj,
            "cnpjs_mascarados": sorted(
                mask_cnpj(cnpj) for cnpj in cnpjs
            ),
        }

    return {
        "status": "PONTE_UNIVOCA",
        "codigo_emissor_b3": issuer_code,
        "qtd_linhas_isin": len(isin_rows),
        "qtd_linhas_emissor": len(issuer_rows),
        "cnpj_mascarado": mask_cnpj(next(iter(cnpjs))),
        "nomes_emissor": sorted({
            row["nome_emissor"] for row in issuer_rows
        })[:5],
    }


def build_audit(
    by_isin: dict[str, list[dict]],
    by_code: dict[str, list[dict]],
):
    targets = {
        ticker: {
            "isin": isin,
            **unique_bridge_for(isin, by_isin, by_code),
        }
        for ticker, isin in AUDIT_TARGETS.items()
    }

    duplicate_samples = []
    for code, items in sorted(
        (
            (code, rows)
            for code, rows in by_code.items()
            if len(rows) > 1
        ),
        key=lambda item: (-len(item[1]), item[0]),
    )[:10]:
        duplicate_samples.append({
            "codigo_emissor_b3": code,
            "qtd_linhas": len(items),
            "qtd_cnpjs_distintos": len({
                row["cnpj_emissor_normalizado"]
                for row in items
                if row["cnpj_emissor_normalizado"]
            }),
            "cnpjs_mascarados": sorted({
                mask_cnpj(row["cnpj_emissor_normalizado"])
                for row in items
                if row["cnpj_emissor_normalizado"]
            }),
            "nomes": sorted({
                row["nome_emissor"] for row in items
            })[:5],
        })

    return {
        "alvos": targets,
        "amostra_codigos_emissor_duplicados": duplicate_samples,
    }


def check_variation(
    current: int,
    previous: int,
    label: str,
):
    if previous <= 0:
        return
    ratio = abs(current - previous) / previous
    if ratio > MAX_TOTAL_VARIATION_RATIO:
        raise B3IsinError(
            f"variação anormal em {label}: "
            f"anterior={previous} atual={current} "
            f"variacao={ratio:.2%}"
        )


def baseline_counts(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                (select count(*)
                   from investimento.b3_isin_emissores),
                (select count(*)
                   from investimento.b3_isin_cadastro)
            """
        )
        emitters, isins = cur.fetchone()
    return int(emitters), int(isins)


def copy_rows(
    cur,
    table: str,
    columns: tuple[str, ...],
    rows: list[dict],
):
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
                    value = json.dumps(
                        value, ensure_ascii=False
                    )
                values.append(value)
            copy.write_row(values)


def replace_snapshot(conn, emitters, isins):
    emitter_columns = (
        "codigo_emissor_b3",
        "nome_emissor",
        "cnpj_emissor_normalizado",
        "campo_4_oficial",
        "linha_arquivo",
        "data_referencia",
        "dados_raw",
    )
    isin_columns = (
        "isin",
        "codigo_emissor_b3",
        "linha_arquivo",
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
                (like investimento.b3_isin_emissores
                 including defaults)
                on commit drop
                """
            )
            cur.execute(
                """
                create temp table tmp_b3_isin_cadastro
                (like investimento.b3_isin_cadastro
                 including defaults)
                on commit drop
                """
            )

            copy_rows(
                cur,
                "tmp_b3_isin_emissores",
                emitter_columns,
                emitters,
            )
            copy_rows(
                cur,
                "tmp_b3_isin_cadastro",
                isin_columns,
                isins,
            )

            cur.execute(
                "truncate investimento.b3_isin_cadastro"
            )
            cur.execute(
                "truncate investimento.b3_isin_emissores"
            )

            cur.execute(
                f"""
                insert into investimento.b3_isin_emissores
                    ({', '.join(emitter_columns)})
                select {', '.join(emitter_columns)}
                from tmp_b3_isin_emissores
                """
            )
            cur.execute(
                f"""
                insert into investimento.b3_isin_cadastro
                    ({', '.join(isin_columns)})
                select {', '.join(isin_columns)}
                from tmp_b3_isin_cadastro
                """
            )


def audit_funds_database(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                count(*) as fundos_b3,
                count(p.isin) as ponte_univoca_b3
            from investimento.ativos a
            left join investimento.vw_b3_isin_ponte_unica p
              on p.isin = a.isin
            where a.classe = 'FUNDO'
              and a.instrumento_canonico = true
              and a.atividade_confirmada_b3 = true
            """
        )
        fundos_b3, ponte = cur.fetchone()

        cur.execute(
            """
            select
                a.ticker,
                a.isin,
                p.codigo_emissor_b3,
                p.cnpj_emissor_normalizado is not null
                    as possui_cnpj_b3
            from investimento.ativos a
            left join investimento.vw_b3_isin_ponte_unica p
              on p.isin = a.isin
            where a.ticker = any(%s)
            order by a.ticker
            """,
            (list(AUDIT_TARGETS),),
        )
        examples = [
            {
                "ticker": row[0],
                "isin": row[1],
                "codigo_emissor_b3": row[2],
                "possui_ponte_cnpj_b3": row[3],
            }
            for row in cur.fetchall()
        ]

    return {
        "fundos_b3": int(fundos_b3),
        "ponte_univoca_b3": int(ponte),
        "exemplos": examples,
    }


def create_log(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo,
                processo,
                status,
                iniciado_em
            )
            values (%s, %s, 'INICIADO', now())
            returning id
            """,
            (SOURCE_CODE, PROCESS_NAME),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def finish_log(
    conn,
    log_id,
    status,
    read_count=0,
    written_count=0,
    message=None,
):
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
                (
                    json.dumps(message, ensure_ascii=False)
                    if message is not None
                    else None
                ),
                log_id,
            ),
        )
    conn.commit()


def collect_official_data():
    zip_bytes, reference_date = download_official_zip()
    emissor_bytes, numeraca_bytes = extract_official_payloads(
        zip_bytes
    )

    emitters, by_code, emitter_metrics = parse_emissores(
        emissor_bytes, reference_date
    )
    isins, by_isin, isin_metrics = parse_isins(
        numeraca_bytes,
        set(by_code),
        reference_date,
    )
    audit = build_audit(by_isin, by_code)

    return {
        "reference_date": reference_date,
        "emitters": emitters,
        "isins": isins,
        "emitter_metrics": emitter_metrics,
        "isin_metrics": isin_metrics,
        "audit": audit,
    }


def main():
    conn = connect()
    log_id = create_log(conn)
    read_count = 0

    try:
        progress(
            "baixando Banco de Dados Completo ISIN oficial"
        )
        data = collect_official_data()
        emitters = data["emitters"]
        isins = data["isins"]
        read_count = len(emitters) + len(isins)

        previous_emitters, previous_isins = baseline_counts(conn)
        check_variation(
            len(emitters), previous_emitters, "emissores"
        )
        check_variation(
            len(isins), previous_isins, "ISINs"
        )

        progress(
            "sanidade aprovada antes de qualquer substituição | "
            f"emissores={len(emitters)} "
            f"isins={len(isins)} "
            f"ref={data['reference_date']}"
        )

        replace_snapshot(conn, emitters, isins)
        db_audit = audit_funds_database(conn)

        metrics = {
            "data_referencia": str(data["reference_date"]),
            "emissores": data["emitter_metrics"],
            "isins": data["isin_metrics"],
            "auditoria_sem_banco": data["audit"],
            "auditoria_fundos_banco": db_audit,
        }

        finish_log(
            conn,
            log_id,
            "SUCESSO",
            read_count=read_count,
            written_count=read_count,
            message=metrics,
        )
        progress(
            "SUCESSO | "
            + json.dumps(metrics, ensure_ascii=False)
        )
    except Exception as exc:
        finish_log(
            conn,
            log_id,
            "ERRO",
            read_count=read_count,
            written_count=0,
            message={"erro": str(exc)},
        )
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
