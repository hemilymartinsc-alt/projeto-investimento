from __future__ import annotations

import csv
import io
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from src.database import connect

SOURCE_CODE = "B3_INSTRUMENTS"
PROCESS_NAME = "validar_classificar_instrumentos_b3"
REQUEST_URL = "https://arquivos.b3.com.br/api/download/requestname"
DOWNLOAD_URL = "https://arquivos.b3.com.br/api/download/"
SOURCE_URL = "https://arquivos.b3.com.br/"
TZ_BR = ZoneInfo("America/Sao_Paulo")
ISIN_RE = re.compile(r"^[A-Z0-9]{12}$")
REQUEST_TIMEOUT = (5, 15)
DOWNLOAD_TIMEOUT = (5, 30)

SNAPSHOT_COLUMNS = (
    "data_referencia",
    "ticker",
    "isin",
    "ativo_base",
    "descricao_ativo",
    "segmento_b3",
    "mercado_b3",
    "categoria_b3",
    "descricao_b3",
    "cfi_code",
    "moeda",
    "nome_corporativo",
    "nivel_governanca",
    "data_inicio_negociacao",
    "data_fim_negociacao",
    "data_expiracao",
    "status_arquivo",
    "raw_json",
)

CANDIDATE_COLUMNS = (
    "ticker",
    "isin",
    "nome",
    "nome_pregao",
    "classe",
    "subclasse",
    "tipo_instrumento",
    "categoria_b3",
    "segmento_b3",
    "mercado_b3",
    "moeda",
)


def progress(message: str) -> None:
    print(f"B3 instrumentos: {message}", flush=True)


def clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def upper(v):
    s = clean(v)
    return s.upper() if s else None


def norm(v):
    s = upper(v) or ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(v):
    s = clean(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def pick(row, *keys):
    for key in keys:
        if key in row and clean(row[key]):
            return clean(row[key])
    return None


def parse_csv(content: bytes):
    text = content.decode("iso-8859-1", errors="replace")
    lines = text.splitlines()
    status = None
    header = None
    for index, line in enumerate(lines):
        normalized = norm(line)
        if "STATUS DO ARQUIVO" in normalized:
            status = (
                "Final"
                if "FINAL" in normalized
                else ("Parcial" if "PARCIAL" in normalized else None)
            )
        fields = [
            item.strip()
            for item in next(csv.reader([line], delimiter=";"))
        ]
        if "TckrSymb" in fields and "ISIN" in fields:
            header = index
            break
    if header is None:
        raise RuntimeError("Cabeçalho TckrSymb/ISIN não encontrado no arquivo B3.")
    reader = csv.DictReader(
        io.StringIO("\n".join(lines[header:])), delimiter=";"
    )
    return status, [dict(row) for row in reader if upper(row.get("TckrSymb"))]


def download_latest():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 projeto-investimento/1.0",
            "Referer": SOURCE_URL,
        }
    )
    today = datetime.now(TZ_BR).date()
    errors = []

    for back in range(11):
        ref = today - timedelta(days=back)
        progress(f"consultando InstrumentsConsolidated de {ref}")
        try:
            response = session.get(
                REQUEST_URL,
                params={
                    "fileName": "InstrumentsConsolidated",
                    "date": ref.isoformat(),
                },
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in (400, 404):
                progress(f"arquivo indisponível para {ref}; tentando data anterior")
                continue
            response.raise_for_status()
            token = response.json().get("token")
            if not token:
                progress(f"resposta sem token para {ref}; tentando data anterior")
                continue

            progress(f"baixando arquivo oficial de {ref}")
            file_response = session.get(
                DOWNLOAD_URL,
                params={"token": token},
                timeout=DOWNLOAD_TIMEOUT,
            )
            file_response.raise_for_status()
            status, rows = parse_csv(file_response.content)
            if status == "Parcial":
                progress(f"arquivo de {ref} ainda parcial; tentando data anterior")
                continue
            if rows:
                progress(
                    f"arquivo final encontrado: data={ref} linhas={len(rows)}"
                )
                return ref, status, rows
        except Exception as exc:
            errors.append(f"{ref}: {exc}")
            progress(f"falha em {ref}: {exc}")

    raise RuntimeError(
        "Não foi possível obter arquivo final recente da B3. "
        + "; ".join(errors[-3:])
    )


def normalize(row, ref, status):
    return {
        "data_referencia": parse_date(pick(row, "RptDt", "ReportDate")) or ref,
        "ticker": upper(pick(row, "TckrSymb", "TickerSymbol")),
        "isin": upper(pick(row, "ISIN")),
        "ativo_base": clean(pick(row, "Asst", "Asset")),
        "descricao_ativo": clean(
            pick(row, "AsstDesc", "AssetDescription")
        ),
        "segmento_b3": clean(pick(row, "SgmtNm", "SegmentName")),
        "mercado_b3": clean(pick(row, "MktNm", "MarketName")),
        "categoria_b3": clean(
            pick(row, "SctyCtgyNm", "SecurityCategoryName")
        ),
        "descricao_b3": clean(pick(row, "Desc", "Description")),
        "cfi_code": clean(pick(row, "CFICd", "CFICode")),
        "moeda": upper(pick(row, "TrdgCcy", "TradingCurrency")),
        "nome_corporativo": clean(
            pick(row, "CrpnNm", "CorporateName", "CorpName")
        ),
        "nivel_governanca": clean(
            pick(
                row,
                "CorpGovnLvlNm",
                "CorpGovnLvlNam",
                "CorporateGovernanceLevelName",
            )
        ),
        "data_inicio_negociacao": parse_date(
            pick(row, "TrdgStartDt", "TradingStartDate")
        ),
        "data_fim_negociacao": parse_date(
            pick(row, "TrdgEndDt", "TradingEndDate")
        ),
        "data_expiracao": parse_date(pick(row, "XprtnDt", "ExpirationDate")),
        "status_arquivo": status,
        "raw_json": row,
    }


def text_of(inst):
    fields = (
        "categoria_b3",
        "descricao_b3",
        "descricao_ativo",
        "segmento_b3",
        "mercado_b3",
        "nome_corporativo",
        "cfi_code",
    )
    return " | ".join(norm(inst.get(key)) for key in fields if inst.get(key))


def derivative(inst):
    text = text_of(inst)
    return any(
        term in text
        for term in (
            "OPTION",
            "OPCAO",
            "FUTURE",
            "FUTURO",
            "FORWARD",
            "TERMO",
            "SWAP",
            "SECURITY LENDING",
            "EMPRESTIMO",
            "EXERCISE",
            "EXERCICIO",
        )
    )


def operational(inst):
    text = text_of(inst)
    return any(
        term in text
        for term in (
            "ODD LOT",
            "FRACIONARIO",
            "FRACTIONAL",
            "BLOCK LOT",
            "LOTE EM BLOCO",
            "AUCTION",
            "LEILAO",
            "SUBSCRIPTION RIGHT",
            "DIREITO DE SUBSCRICAO",
            "SUBSCRIPTION RECEIPT",
            "RECIBO DE SUBSCRICAO",
        )
    )


def classify(inst):
    text = text_of(inst)
    if "BDR" in text or "BRAZILIAN DEPOSITARY" in text:
        return "BDR", ("ETF_BDR" if "ETF" in text else None)
    if any(
        term in text
        for term in (
            "FUNDO IMOBILI",
            "REAL ESTATE FUND",
            " FII ",
            "FII|",
            "|FII",
        )
    ):
        return "FII", None
    if "ETF" in text or "EXCHANGE TRADED FUND" in text:
        return "ETF", None
    if "ETP" in text or "EXCHANGE TRADED PRODUCT" in text:
        return "ETP", None
    if any(
        term in text
        for term in (
            "ORDINARY SHARES",
            "PREFERRED SHARES",
            "COMMON SHARES",
            "ACAO ORDINARIA",
            "ACAO PREFERENCIAL",
            "ACOES ORDINARIAS",
            "ACOES PREFERENCIAIS",
            "STOCK",
            "SHARES",
        )
    ):
        if "UNIT" in text:
            return "ACAO", "UNIT"
        if "PREFERRED" in text or "PREFERENCIAL" in text:
            return "ACAO", "PN"
        if "ORDINARY" in text or "ORDINARIA" in text:
            return "ACAO", "ON"
        return "ACAO", None
    if any(
        term in text
        for term in ("FIXED INCOME", "RENDA FIXA", "DEBENTURE", "BOND")
    ):
        return "RENDA_FIXA", None
    if "FUND" in text or "FUNDO" in text:
        return "FUNDO", None
    return "OUTRO", None


def current(inst, ref):
    end = inst.get("data_fim_negociacao")
    expiration = inst.get("data_expiracao")
    return not (
        (end and end < ref) or (expiration and expiration < ref)
    )


def candidate(inst, ref):
    isin = inst.get("isin")
    return bool(
        inst.get("ticker")
        and isin
        and ISIN_RE.fullmatch(isin)
        and current(inst, ref)
        and not derivative(inst)
        and not operational(inst)
    )


def snapshot_row(inst):
    return (
        inst["data_referencia"],
        inst["ticker"],
        inst["isin"],
        inst.get("ativo_base"),
        inst.get("descricao_ativo"),
        inst.get("segmento_b3"),
        inst.get("mercado_b3"),
        inst.get("categoria_b3"),
        inst.get("descricao_b3"),
        inst.get("cfi_code"),
        inst.get("moeda"),
        inst.get("nome_corporativo"),
        inst.get("nivel_governanca"),
        inst.get("data_inicio_negociacao"),
        inst.get("data_fim_negociacao"),
        inst.get("data_expiracao"),
        inst.get("status_arquivo"),
        json.dumps(inst.get("raw_json") or {}, ensure_ascii=False),
    )


def copy_rows(cur, table, columns, rows):
    column_list = ", ".join(columns)
    with cur.copy(f"copy {table} ({column_list}) from stdin") as copy:
        for row in rows:
            copy.write_row(row)


def save_snapshot(conn, instruments, ref):
    progress(f"carregando snapshot em lote: linhas={len(instruments)}")
    with conn.cursor() as cur:
        cur.execute(
            """
            create temporary table tmp_b3_snapshot (
                data_referencia date not null,
                ticker text not null,
                isin text not null,
                ativo_base text,
                descricao_ativo text,
                segmento_b3 text,
                mercado_b3 text,
                categoria_b3 text,
                descricao_b3 text,
                cfi_code text,
                moeda text,
                nome_corporativo text,
                nivel_governanca text,
                data_inicio_negociacao date,
                data_fim_negociacao date,
                data_expiracao date,
                status_arquivo text,
                raw_json jsonb
            ) on commit drop
            """
        )
        if instruments:
            copy_rows(
                cur,
                "tmp_b3_snapshot",
                SNAPSHOT_COLUMNS,
                (snapshot_row(inst) for inst in instruments),
            )
        cur.execute(
            """
            delete from investimento.b3_instrumentos_snapshot
             where data_referencia = %s
            """,
            (ref,),
        )
        cur.execute(
            """
            insert into investimento.b3_instrumentos_snapshot (
                data_referencia, ticker, isin, ativo_base, descricao_ativo,
                segmento_b3, mercado_b3, categoria_b3, descricao_b3,
                cfi_code, moeda, nome_corporativo, nivel_governanca,
                data_inicio_negociacao, data_fim_negociacao, data_expiracao,
                status_arquivo, raw_json
            )
            select
                data_referencia, ticker, isin, ativo_base, descricao_ativo,
                segmento_b3, mercado_b3, categoria_b3, descricao_b3,
                cfi_code, moeda, nome_corporativo, nivel_governanca,
                data_inicio_negociacao, data_fim_negociacao, data_expiracao,
                status_arquivo, raw_json
            from tmp_b3_snapshot
            """
        )
        inserted = cur.rowcount
    progress(f"snapshot persistido em operação set-based: linhas={inserted}")
    return inserted


def load_existing(conn):
    with conn.cursor() as cur:
        cur.execute("select ticker, isin from investimento.ativos")
        return {upper(row[0]): upper(row[1]) for row in cur.fetchall()}


def best(rows, target_isin=None):
    if target_isin:
        exact = [row for row in rows if upper(row.get("isin")) == target_isin]
        if exact:
            rows = exact

    def score(inst):
        text = text_of(inst)
        return (
            (10 if not derivative(inst) and not operational(inst) else 0)
            + (5 if any(term in text for term in ("SPOT", "CASH", "VISTA")) else 0)
            + (1 if inst.get("categoria_b3") else 0)
        )

    return max(rows, key=score) if rows else None


def candidate_row(inst):
    classe, subclasse = classify(inst)
    ticker = inst["ticker"]
    nome = (
        inst.get("nome_corporativo")
        or inst.get("descricao_ativo")
        or inst.get("descricao_b3")
        or ticker
    )
    return (
        ticker,
        upper(inst["isin"]),
        nome,
        inst.get("ativo_base"),
        classe,
        subclasse,
        inst.get("descricao_b3"),
        inst.get("categoria_b3"),
        inst.get("segmento_b3"),
        inst.get("mercado_b3"),
        inst.get("moeda") or "BRL",
    )


def stage_candidates(conn, instruments, ref):
    existing = load_existing(conn)
    by_ticker = {}
    for inst in instruments:
        by_ticker.setdefault(inst["ticker"], []).append(inst)

    candidates = []
    for ticker, rows in by_ticker.items():
        valid = [row for row in rows if candidate(row, ref)]
        selected = best(valid, existing.get(ticker))
        if selected:
            candidates.append(candidate_row(selected))

    progress(f"preparando candidatos válidos: tickers={len(candidates)}")
    with conn.cursor() as cur:
        cur.execute(
            """
            create temporary table tmp_b3_candidates (
                ticker text primary key,
                isin text not null,
                nome text,
                nome_pregao text,
                classe text not null,
                subclasse text,
                tipo_instrumento text,
                categoria_b3 text,
                segmento_b3 text,
                mercado_b3 text,
                moeda text not null
            ) on commit drop
            """
        )
        if candidates:
            copy_rows(cur, "tmp_b3_candidates", CANDIDATE_COLUMNS, candidates)
    return len(candidates)


COUNTS_SQL = """
select
    (select count(*) from tmp_b3_candidates) as candidatos,
    (
        select count(*)
        from tmp_b3_candidates c
        left join investimento.ativos a
          on upper(trim(a.ticker)) = c.ticker
        where a.ticker is null
    ) as novos,
    (
        select count(*)
        from tmp_b3_candidates c
        left join investimento.ativos a
          on upper(trim(a.ticker)) = c.ticker
        where a.ticker is null or upper(trim(a.isin)) = c.isin
    ) as validados,
    (
        select count(*)
        from investimento.ativos a
        join tmp_b3_candidates c
          on c.ticker = upper(trim(a.ticker))
        where a.isin is not null
          and upper(trim(a.isin)) <> c.isin
    ) as divergentes,
    (
        select count(*)
        from investimento.ativos a
        where a.isin is null or trim(a.isin) = ''
    ) as sem_isin,
    (
        select count(*)
        from investimento.ativos a
        where a.isin is not null
          and trim(a.isin) <> ''
          and not exists (
              select 1 from tmp_b3_candidates c
              where c.ticker = upper(trim(a.ticker))
          )
          and exists (
              select 1 from tmp_b3_snapshot s
              where s.ticker = upper(trim(a.ticker))
                and s.isin = upper(trim(a.isin))
                and (s.data_fim_negociacao is null or s.data_fim_negociacao >= %s)
                and (s.data_expiracao is null or s.data_expiracao >= %s)
          )
    ) as duvidosos,
    (
        select count(*)
        from investimento.ativos a
        where a.isin is not null
          and trim(a.isin) <> ''
          and not exists (
              select 1 from tmp_b3_candidates c
              where c.ticker = upper(trim(a.ticker))
          )
          and not exists (
              select 1 from tmp_b3_snapshot s
              where s.ticker = upper(trim(a.ticker))
                and s.isin = upper(trim(a.isin))
                and (s.data_fim_negociacao is null or s.data_fim_negociacao >= %s)
                and (s.data_expiracao is null or s.data_expiracao >= %s)
          )
    ) as inativos
"""


APPLY_MASTER_SQL = """
update investimento.ativos a
   set nome = coalesce(c.nome, a.nome),
       classe = c.classe,
       subclasse = c.subclasse,
       tipo_instrumento = c.tipo_instrumento,
       categoria_b3 = c.categoria_b3,
       segmento_b3 = c.segmento_b3,
       mercado_b3 = c.mercado_b3,
       moeda = coalesce(c.moeda, a.moeda),
       isin = c.isin,
       ativo = true,
       elegivel_analise = false,
       status_validacao = 'VALIDADO_B3',
       motivo_exclusao = 'Validado na B3; aguarda validação oficial complementar obrigatória da classe.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
  from tmp_b3_candidates c
 where c.ticker = upper(trim(a.ticker))
   and a.isin is not null
   and upper(trim(a.isin)) = c.isin;

update investimento.ativos a
   set ativo = false,
       elegivel_analise = false,
       status_validacao = 'DIVERGENTE',
       motivo_exclusao = 'Ticker encontrado na B3 com ISIN diferente do cadastro mestre.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
  from tmp_b3_candidates c
 where c.ticker = upper(trim(a.ticker))
   and a.isin is not null
   and upper(trim(a.isin)) <> c.isin;

update investimento.ativos a
   set ativo = false,
       elegivel_analise = false,
       status_validacao = 'SEM_ISIN',
       motivo_exclusao = 'Ativo sem ISIN válido.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 where a.isin is null or trim(a.isin) = '';

update investimento.ativos a
   set ativo = false,
       elegivel_analise = false,
       status_validacao = 'DUVIDOSO',
       motivo_exclusao = 'Instrumento oficial B3 fora do universo de carteira suportado nesta fase.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 where a.isin is not null
   and trim(a.isin) <> ''
   and not exists (
       select 1 from tmp_b3_candidates c
       where c.ticker = upper(trim(a.ticker))
   )
   and exists (
       select 1 from tmp_b3_snapshot s
       where s.ticker = upper(trim(a.ticker))
         and s.isin = upper(trim(a.isin))
         and (s.data_fim_negociacao is null or s.data_fim_negociacao >= %(ref)s)
         and (s.data_expiracao is null or s.data_expiracao >= %(ref)s)
   );

update investimento.ativos a
   set ativo = false,
       elegivel_analise = false,
       status_validacao = 'INATIVO',
       motivo_exclusao = 'Ticker/ISIN não encontrado como instrumento corrente no último cadastro oficial da B3.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 where a.isin is not null
   and trim(a.isin) <> ''
   and not exists (
       select 1 from tmp_b3_candidates c
       where c.ticker = upper(trim(a.ticker))
   )
   and not exists (
       select 1 from tmp_b3_snapshot s
       where s.ticker = upper(trim(a.ticker))
         and s.isin = upper(trim(a.isin))
         and (s.data_fim_negociacao is null or s.data_fim_negociacao >= %(ref)s)
         and (s.data_expiracao is null or s.data_expiracao >= %(ref)s)
   );

insert into investimento.ativos (
    ticker, nome, nome_pregao, classe, subclasse, tipo_instrumento,
    moeda, ativo, isin, fonte_cadastro, url_fonte, categoria_b3,
    segmento_b3, mercado_b3, status_validacao, elegivel_analise,
    motivo_exclusao, fonte_validacao, validado_em, atualizado_em
)
select
    c.ticker, c.nome, c.nome_pregao, c.classe, c.subclasse,
    c.tipo_instrumento, c.moeda, true, c.isin, %(source_code)s,
    %(source_url)s, c.categoria_b3, c.segmento_b3, c.mercado_b3,
    'VALIDADO_B3', false,
    'Validado na B3; aguarda validação oficial complementar obrigatória da classe.',
    %(source_code)s, now(), now()
from tmp_b3_candidates c
where not exists (
    select 1 from investimento.ativos a
    where upper(trim(a.ticker)) = c.ticker
)
on conflict (ticker) do nothing;
"""

(
    UPDATE_VALIDATED_SQL,
    UPDATE_DIVERGENT_SQL,
    UPDATE_WITHOUT_ISIN_SQL,
    UPDATE_DOUBTFUL_SQL,
    UPDATE_INACTIVE_SQL,
    INSERT_NEW_SQL,
) = APPLY_MASTER_SQL.strip().split(";\n\n")


def validate_master(conn, instruments, ref):
    stage_candidates(conn, instruments, ref)
    progress("calculando resultados e atualizando cadastro mestre em lote")
    with conn.cursor() as cur:
        cur.execute(COUNTS_SQL, (ref, ref, ref, ref))
        columns = [description.name for description in cur.description]
        counts = dict(zip(columns, cur.fetchone()))
        params = {
            "source_code": SOURCE_CODE,
            "source_url": SOURCE_URL,
            "ref": ref,
        }
        cur.execute(UPDATE_VALIDATED_SQL, params)
        cur.execute(UPDATE_DIVERGENT_SQL, params)
        cur.execute(UPDATE_WITHOUT_ISIN_SQL, params)
        cur.execute(UPDATE_DOUBTFUL_SQL, params)
        cur.execute(UPDATE_INACTIVE_SQL, params)
        cur.execute(INSERT_NEW_SQL, params)
    progress(
        "cadastro mestre atualizado: "
        + " ".join(f"{key}={value}" for key, value in counts.items())
    )
    return counts


def log_start(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (fonte_codigo, processo, status)
            values (%s, %s, 'INICIADO')
            returning id
            """,
            (SOURCE_CODE, PROCESS_NAME),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def log_end(conn, log_id, status, result, message):
    with conn.cursor() as cur:
        cur.execute(
            """
            update investimento.coletas_log
               set finalizado_em = now(),
                   status = %s,
                   registros_lidos = %s,
                   registros_gravados = %s,
                   mensagem = %s
             where id = %s
            """,
            (
                status,
                (result or {}).get("linhas_arquivo"),
                (result or {}).get("snapshot", 0),
                message[:1500],
                log_id,
            ),
        )
    conn.commit()


def main():
    progress("iniciando validação")
    conn = connect()
    log_id = log_start(conn)
    try:
        ref, status, raw = download_latest()
        progress("normalizando arquivo oficial")
        instruments = [normalize(row, ref, status) for row in raw]
        with_isin = [
            inst
            for inst in instruments
            if inst.get("isin") and ISIN_RE.fullmatch(inst["isin"])
        ]
        progress(
            f"normalização concluída: linhas={len(raw)} com_isin={len(with_isin)}"
        )
        snapshot_count = save_snapshot(conn, with_isin, ref)
        counts = validate_master(conn, with_isin, ref)
        conn.commit()
        result = {
            "data_referencia": str(ref),
            "status_arquivo": status,
            "linhas_arquivo": len(raw),
            "linhas_com_isin": len(with_isin),
            "snapshot": snapshot_count,
            **counts,
        }
        message = json.dumps(result, ensure_ascii=False, sort_keys=True)
        log_end(conn, log_id, "SUCESSO", result, message)
        progress(f"SUCESSO | {message}")
        return 0
    except Exception as exc:
        conn.rollback()
        try:
            log_end(conn, log_id, "ERRO", None, str(exc))
        finally:
            print(f"B3 instrumentos: erro | {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
