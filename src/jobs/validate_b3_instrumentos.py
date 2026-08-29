from __future__ import annotations

import csv
import io
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
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

CANONICAL_SEGMENT = "CASH"
CANONICAL_MARKET = "EQUITY-CASH"

# Exceção oficial e auditável para ETFs de renda fixa. O registro negociável
# só é aceito quando possui o mesmo ISIN e ativo-base de um registro técnico
# ETF PRIMARY MARKET presente no próprio InstrumentsConsolidated.
FIXED_INCOME_ETF_SEGMENT = "FORWARD"
FIXED_INCOME_ETF_MARKET = "FIXED INCOME"
FIXED_INCOME_ETF_CATEGORY = "FIXED INCOME TRADABLE INSTRUMENT T1"
FIXED_INCOME_ETF_PRIMARY_SEGMENT = "ETF PRIMARY MARKET"
FIXED_INCOME_ETF_PRIMARY_CATEGORY_PREFIX = "ETF PRIMARY MARKET"

NON_CANONICAL_SEGMENTS = {
    "EQUITY BLOCK TRADING LOT": "EQUITY_BLOCK_TRADING_LOT",
    "ETF PRIMARY MARKET": "ETF_PRIMARY_MARKET",
    "ODD LOT": "ODD_LOT",
}

NON_CANONICAL_CATEGORIES = {
    "RIGHTS": "RIGHTS",
    "RECEIPTS": "RECEIPTS",
    "WARRANT": "WARRANT",
    "INDEX": "INDEX",
}

DERIVATIVE_SEGMENTS = {
    "AGRIBUSINESS",
    "EQUITY CALL",
    "EQUITY FORWARD",
    "EQUITY PUT",
    "FINANCIAL",
    "FORWARD",
}

DERIVATIVE_MARKETS = {
    "EQUITY-DERIVATE",
    "FORWARD",
    "FUTURE",
    "OPTIONS ON FUTURE",
    "OPTIONS ON SPOT",
}

DERIVATIVE_CATEGORY_TERMS = (
    "OPTION",
    "FUTURE",
    "FORWARD",
    "SWAP",
    "SECURITY LENDING",
    "EXERCISE",
)

B3_NULL_DATE = datetime.max.date()

# Variantes listadas que devem ser registradas no mestre para que recebam
# explicitamente o status NAO_CANONICO. Derivativos continuam apenas no snapshot.
MASTER_NON_CANONICAL_VARIANTS = frozenset(
    {
        *NON_CANONICAL_SEGMENTS.values(),
        *NON_CANONICAL_CATEGORIES.values(),
    }
)

AUDIT_TICKERS = (
    "PETR4",
    "VALE3",
    "SANB11",
    "HGLG11",
    "KNRI11",
    "BOVA11",
    "IVVB11",
    "AADA39",
    "AFHI11",
    "AFHI11M",
    "LFTI11",
    "LFTS11",
    "IMAB11",
    "B5P211",
    "003H11",
    "0FEA11",
    "2WAV3",
)

SNAPSHOT_COPY_COLUMNS = (
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
    "instrumento_canonico",
    "tipo_variante_b3",
    "ticker_canonico",
    "classe_preliminar",
    "subclasse_preliminar",
    "corrente",
    "motivo_validacao_b3",
    "isin_valido",
    "em_escopo_mestre",
)


def progress(message: str) -> None:
    print(f"B3 instrumentos: {message}", flush=True)


def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def upper(value):
    text = clean(value)
    return text.upper() if text else None


def norm(value):
    text = upper(value) or ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    text = clean(value)
    if not text:
        return None
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], date_format).date()
        except ValueError:
            pass
    return None


def parse_b3_date(value):
    parsed = parse_date(value)
    return None if parsed == B3_NULL_DATE else parsed


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
    # O ticker é obrigatório no schema bruto e identifica cada instrumento B3.
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
        "data_inicio_negociacao": parse_b3_date(
            pick(row, "TradgStartDt", "TrdgStartDt", "TradingStartDate")
        ),
        "data_fim_negociacao": parse_b3_date(
            pick(row, "TradgEndDt", "TrdgEndDt", "TradingEndDate")
        ),
        "data_expiracao": parse_b3_date(
            pick(row, "XprtnDt", "ExpirationDate")
        ),
        "status_arquivo": status,
        "raw_json": row,
    }


def derivative(inst):
    segment = norm(inst.get("segmento_b3"))
    market = norm(inst.get("mercado_b3"))
    category = norm(inst.get("categoria_b3"))
    if segment in DERIVATIVE_SEGMENTS or market in DERIVATIVE_MARKETS:
        return True
    return any(term in category for term in DERIVATIVE_CATEGORY_TERMS)


def activity_validation_issue(inst, ref):
    start = inst.get("data_inicio_negociacao")
    end = inst.get("data_fim_negociacao")
    expiration = inst.get("data_expiracao")

    if (end and end < ref) or (expiration and expiration < ref):
        return "INATIVO_B3"
    if start is None:
        return "DATA_INICIO_NAO_INFORMADA_B3"
    if start > ref:
        return "INICIO_NEGOCIACAO_FUTURO"
    return None


def current(inst, ref):
    return activity_validation_issue(inst, ref) is None


def valid_isin(inst):
    isin = inst.get("isin")
    return bool(isin and ISIN_RE.fullmatch(isin))


def fixed_income_etf_pair_key(inst):
    isin = upper(inst.get("isin"))
    asset = norm(inst.get("ativo_base"))
    if not isin or not asset:
        return None
    return isin, asset


def official_fixed_income_etf_keys(instruments):
    return {
        key
        for inst in instruments
        if (
            norm(inst.get("segmento_b3"))
            == FIXED_INCOME_ETF_PRIMARY_SEGMENT
            and norm(inst.get("mercado_b3")) == FIXED_INCOME_ETF_MARKET
            and norm(inst.get("categoria_b3")).startswith(
                FIXED_INCOME_ETF_PRIMARY_CATEGORY_PREFIX
            )
            and (key := fixed_income_etf_pair_key(inst)) is not None
        )
    }


def is_fixed_income_etf_candidate(inst):
    return bool(
        norm(inst.get("segmento_b3")) == FIXED_INCOME_ETF_SEGMENT
        and norm(inst.get("mercado_b3")) == FIXED_INCOME_ETF_MARKET
        and norm(inst.get("categoria_b3")) == FIXED_INCOME_ETF_CATEGORY
    )


def is_official_fixed_income_etf(inst, fixed_income_etf_keys):
    return bool(
        is_fixed_income_etf_candidate(inst)
        and fixed_income_etf_pair_key(inst) in fixed_income_etf_keys
    )


def preliminary_classification(inst):
    category = norm(inst.get("categoria_b3"))
    official_name = norm(inst.get("nome_corporativo"))

    if inst.get("etf_renda_fixa"):
        return "ETF", "RENDA_FIXA"
    if category == "SHARES":
        return "ACAO", None
    if category == "UNIT":
        return "ACAO", "UNIT"
    if category == "BDR":
        if re.search(r"\bETP\b", official_name):
            return "BDR", "ETP"
        if re.search(r"\bETF\b", official_name):
            return "BDR", "ETF"
        return "BDR", None
    if category.startswith("ETF"):
        return "ETF", "RENDA_VARIAVEL"
    if category == "FUNDS":
        return "FUNDO", None
    if category in {"ETP", "EXCHANGE TRADED PRODUCT"}:
        return "ETP", None
    return "OUTRO", None


def canonical_decision(inst, _ref, fixed_income_etf_keys=frozenset()):
    segment = norm(inst.get("segmento_b3"))
    market = norm(inst.get("mercado_b3"))
    category = norm(inst.get("categoria_b3"))

    if segment in NON_CANONICAL_SEGMENTS:
        return False, NON_CANONICAL_SEGMENTS[segment]

    if is_fixed_income_etf_candidate(inst):
        if not is_official_fixed_income_etf(inst, fixed_income_etf_keys):
            return False, "ETF_RENDA_FIXA_SEM_VINCULO_OFICIAL"
        if not inst.get("ticker"):
            return False, "SEM_TICKER"
        if not valid_isin(inst):
            return False, "SEM_ISIN"
        return True, None

    if derivative(inst):
        return False, "DERIVATIVO"

    canonical_market = (
        segment == CANONICAL_SEGMENT and market == CANONICAL_MARKET
    )

    if not canonical_market:
        if segment != CANONICAL_SEGMENT:
            return False, "SEGMENTO_NAO_CANONICO"
        return False, "MERCADO_NAO_CANONICO"
    if category in NON_CANONICAL_CATEGORIES:
        return False, NON_CANONICAL_CATEGORIES[category]
    if not inst.get("ticker"):
        return False, "SEM_TICKER"
    if not valid_isin(inst):
        return False, "SEM_ISIN"
    return True, None


def annotate_universe(instruments, ref):
    canonical_tickers_by_isin = defaultdict(set)
    fixed_income_etf_keys = official_fixed_income_etf_keys(instruments)

    for inst in instruments:
        inst["etf_renda_fixa"] = is_official_fixed_income_etf(
            inst, fixed_income_etf_keys
        )
        is_canonical, variant = canonical_decision(
            inst, ref, fixed_income_etf_keys
        )
        classe, subclasse = preliminary_classification(inst)
        inst["instrumento_canonico"] = is_canonical
        inst["tipo_variante_b3"] = variant
        inst["classe_preliminar"] = classe
        inst["subclasse_preliminar"] = subclasse
        validation_issue = activity_validation_issue(inst, ref)
        inst["corrente"] = validation_issue is None
        inst["motivo_validacao_b3"] = (
            validation_issue if is_canonical else None
        )
        inst["isin_valido"] = valid_isin(inst)
        if is_canonical:
            canonical_tickers_by_isin[inst["isin"]].add(inst["ticker"])

    for inst in instruments:
        if inst["instrumento_canonico"]:
            inst["ticker_canonico"] = inst["ticker"]
        else:
            related = canonical_tickers_by_isin.get(inst.get("isin"), set())
            inst["ticker_canonico"] = (
                next(iter(related)) if len(related) == 1 else None
            )

        inst["em_escopo_mestre"] = bool(
            inst["isin_valido"]
            and (
                inst["instrumento_canonico"]
                or inst["tipo_variante_b3"]
                in MASTER_NON_CANONICAL_VARIANTS
            )
        )

    return instruments


def snapshot_row(inst):
    return (
        inst["data_referencia"],
        inst["ticker"],
        inst.get("isin"),
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
        inst["instrumento_canonico"],
        inst.get("tipo_variante_b3"),
        inst.get("ticker_canonico"),
        inst["classe_preliminar"],
        inst.get("subclasse_preliminar"),
        inst["corrente"],
        inst.get("motivo_validacao_b3"),
        inst["isin_valido"],
        inst["em_escopo_mestre"],
    )


def copy_rows(cur, table, columns, rows):
    column_list = ", ".join(columns)
    with cur.copy(f"copy {table} ({column_list}) from stdin") as copy:
        for row in rows:
            copy.write_row(row)


def save_snapshot(conn, instruments, ref):
    progress(f"carregando snapshot bruto em lote: linhas={len(instruments)}")
    with conn.cursor() as cur:
        cur.execute(
            """
            create temporary table tmp_b3_snapshot (
                data_referencia date not null,
                ticker text not null,
                isin text,
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
                raw_json jsonb,
                instrumento_canonico boolean not null,
                tipo_variante_b3 text,
                ticker_canonico text,
                classe_preliminar text not null,
                subclasse_preliminar text,
                corrente boolean not null,
                motivo_validacao_b3 text,
                isin_valido boolean not null,
                em_escopo_mestre boolean not null
            ) on commit drop
            """
        )
        if instruments:
            copy_rows(
                cur,
                "tmp_b3_snapshot",
                SNAPSHOT_COPY_COLUMNS,
                (snapshot_row(inst) for inst in instruments),
            )

        cur.execute(
            "create index on tmp_b3_snapshot (ticker, isin)"
        )
        cur.execute(
            "create index on tmp_b3_snapshot (instrumento_canonico, corrente)"
        )
        cur.execute("analyze tmp_b3_snapshot")

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
                status_arquivo, raw_json, instrumento_canonico,
                tipo_variante_b3, ticker_canonico
            )
            select
                data_referencia, ticker, isin, ativo_base, descricao_ativo,
                segmento_b3, mercado_b3, categoria_b3, descricao_b3,
                cfi_code, moeda, nome_corporativo, nivel_governanca,
                data_inicio_negociacao, data_fim_negociacao, data_expiracao,
                status_arquivo, raw_json, instrumento_canonico,
                tipo_variante_b3, ticker_canonico
            from tmp_b3_snapshot
            """
        )
        inserted = cur.rowcount

        cur.execute(
            """
            create temporary table tmp_b3_decisions as
            select distinct on (ticker)
                ticker,
                isin,
                coalesce(
                    nome_corporativo,
                    descricao_ativo,
                    descricao_b3,
                    ticker
                ) as nome,
                ativo_base as nome_pregao,
                classe_preliminar,
                subclasse_preliminar,
                categoria_b3,
                segmento_b3,
                mercado_b3,
                moeda,
                instrumento_canonico,
                tipo_variante_b3,
                ticker_canonico,
                corrente,
                motivo_validacao_b3,
                isin_valido,
                em_escopo_mestre
            from tmp_b3_snapshot
            order by
                ticker,
                instrumento_canonico desc,
                corrente desc,
                em_escopo_mestre desc,
                isin nulls last
            """
        )
        cur.execute(
            "create unique index on tmp_b3_decisions (ticker)"
        )
        cur.execute("create index on tmp_b3_decisions (isin)")
        cur.execute("analyze tmp_b3_decisions")

    progress(f"snapshot bruto persistido: linhas={inserted}")
    return inserted


def build_audit(instruments):
    canonical_classes = Counter()
    confirmed_canonical_classes = Counter()
    pending_start_classes = Counter()
    canonical_etfs = Counter()
    non_canonical_reasons = Counter()
    examples = defaultdict(list)
    requested = {ticker: None for ticker in AUDIT_TICKERS}

    for inst in instruments:
        if inst["instrumento_canonico"]:
            canonical_classes[inst["classe_preliminar"]] += 1
            if inst["corrente"]:
                confirmed_canonical_classes[inst["classe_preliminar"]] += 1
            elif (
                inst.get("motivo_validacao_b3")
                == "DATA_INICIO_NAO_INFORMADA_B3"
            ):
                pending_start_classes[inst["classe_preliminar"]] += 1
            if inst["classe_preliminar"] == "ETF":
                tipo_etf = (
                    "RENDA_FIXA"
                    if inst.get("etf_renda_fixa")
                    else "RENDA_VARIAVEL"
                )
                canonical_etfs[tipo_etf] += 1
        else:
            reason = inst.get("tipo_variante_b3") or "NAO_INFORMADO"
            non_canonical_reasons[reason] += 1
            if len(examples[reason]) < 5:
                examples[reason].append(
                    {
                        "ticker": inst["ticker"],
                        "segmento_b3": inst.get("segmento_b3"),
                        "mercado_b3": inst.get("mercado_b3"),
                        "categoria_b3": inst.get("categoria_b3"),
                        "ticker_canonico": inst.get("ticker_canonico"),
                    }
                )

        ticker = inst["ticker"]
        if ticker in requested and requested[ticker] is None:
            requested[ticker] = {
                "encontrado": True,
                "instrumento_canonico": inst["instrumento_canonico"],
                "tipo_variante_b3": inst.get("tipo_variante_b3"),
                "atividade_b3_confirmada": inst["corrente"],
                "motivo_validacao_b3": inst.get("motivo_validacao_b3"),
                "status_validacao_proposto": (
                    "VALIDADO_B3"
                    if inst["instrumento_canonico"] and inst["corrente"]
                    else (
                        "DUVIDOSO"
                        if inst["instrumento_canonico"]
                        else "NAO_CANONICO"
                    )
                ),
                "ticker_canonico": inst.get("ticker_canonico"),
                "classe_preliminar": inst["classe_preliminar"],
                "subclasse_preliminar": inst.get("subclasse_preliminar"),
                "tipo_etf_preliminar": (
                    "RENDA_FIXA"
                    if inst.get("etf_renda_fixa")
                    else (
                        "RENDA_VARIAVEL"
                        if inst["classe_preliminar"] == "ETF"
                        else None
                    )
                ),
                "segmento_b3": inst.get("segmento_b3"),
                "mercado_b3": inst.get("mercado_b3"),
                "categoria_b3": inst.get("categoria_b3"),
                "isin": inst.get("isin"),
            }

    for ticker, result in requested.items():
        if result is None:
            requested[ticker] = {"encontrado": False}

    total_canonical = sum(canonical_classes.values())
    total_confirmed = sum(confirmed_canonical_classes.values())
    total_pending_start = sum(pending_start_classes.values())
    return {
        "total_bruto_snapshot": len(instruments),
        "total_canonico": total_canonical,
        "total_nao_canonico": len(instruments) - total_canonical,
        "distribuicao_canonica_classe": dict(
            sorted(canonical_classes.items())
        ),
        "total_canonicos_confirmados_b3": total_confirmed,
        "distribuicao_canonicos_confirmados_classe": dict(
            sorted(confirmed_canonical_classes.items())
        ),
        "total_canonicos_pendentes_data_inicio": total_pending_start,
        "distribuicao_pendentes_data_inicio_classe": dict(
            sorted(pending_start_classes.items())
        ),
        "total_canonicos_outras_pendencias": (
            total_canonical - total_confirmed - total_pending_start
        ),
        "etfs_canonicos_renda_variavel": canonical_etfs["RENDA_VARIAVEL"],
        "etfs_canonicos_renda_fixa": canonical_etfs["RENDA_FIXA"],
        "etfs_canonicos_total": sum(canonical_etfs.values()),
        "distribuicao_nao_canonicos_motivo": dict(
            non_canonical_reasons.most_common()
        ),
        "exemplos_nao_canonicos": dict(examples),
        "validacao_tickers": requested,
    }


def emit_audit(audit):
    progress(
        "AUDITORIA | "
        f"total_bruto={audit['total_bruto_snapshot']} "
        f"total_canonico={audit['total_canonico']} "
        f"total_nao_canonico={audit['total_nao_canonico']}"
    )
    progress(
        "AUDITORIA | classes_canonicas="
        + json.dumps(
            audit["distribuicao_canonica_classe"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    progress(
        "AUDITORIA | canonicidade_e_atividade "
        f"confirmados_b3={audit['total_canonicos_confirmados_b3']} "
        f"pendentes_data_inicio="
        f"{audit['total_canonicos_pendentes_data_inicio']} "
        f"nao_canonicos_estruturais={audit['total_nao_canonico']}"
    )
    progress(
        "AUDITORIA | pendentes_data_inicio_por_classe="
        + json.dumps(
            audit["distribuicao_pendentes_data_inicio_classe"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    progress(
        "AUDITORIA | ETFs canônicos "
        f"renda_variavel={audit['etfs_canonicos_renda_variavel']} "
        f"renda_fixa={audit['etfs_canonicos_renda_fixa']} "
        f"total={audit['etfs_canonicos_total']}"
    )

    for reason, total in audit["distribuicao_nao_canonicos_motivo"].items():
        example_tickers = [
            item["ticker"]
            for item in audit["exemplos_nao_canonicos"].get(reason, [])
        ]
        progress(
            "AUDITORIA | "
            f"nao_canonico motivo={reason} total={total} "
            f"exemplos={','.join(example_tickers)}"
        )

    for ticker in AUDIT_TICKERS:
        progress(
            f"AUDITORIA | ticker={ticker} "
            + json.dumps(
                audit["validacao_tickers"][ticker],
                ensure_ascii=False,
                sort_keys=True,
            )
        )


COUNTS_SQL = """
select
    (
        select count(*)
        from tmp_b3_decisions d
        where d.instrumento_canonico
          and d.isin_valido
    ) as candidatos_canonicos,
    (
        select count(*)
        from tmp_b3_decisions d
        where d.instrumento_canonico
          and d.corrente
          and d.isin_valido
    ) as canonicos_confirmados_b3,
    (
        select count(*)
        from tmp_b3_decisions d
        where d.instrumento_canonico
          and d.motivo_validacao_b3 = 'DATA_INICIO_NAO_INFORMADA_B3'
          and d.isin_valido
    ) as canonicos_pendentes_data_inicio,
    (
        select count(*)
        from tmp_b3_decisions d
        where not d.instrumento_canonico
    ) as nao_canonicos_estruturais,
    (
        select count(*)
        from tmp_b3_decisions d
        where d.em_escopo_mestre
          and d.instrumento_canonico
          and not exists (
              select 1
              from investimento.ativos a
              where upper(trim(a.ticker)) = d.ticker
          )
    ) as novos_canonicos,
    (
        select count(*)
        from tmp_b3_decisions d
        where d.em_escopo_mestre
          and not d.instrumento_canonico
          and not exists (
              select 1
              from investimento.ativos a
              where upper(trim(a.ticker)) = d.ticker
          )
    ) as novos_nao_canonicos,
    (
        select count(*)
        from tmp_b3_decisions d
        left join investimento.ativos a
          on upper(trim(a.ticker)) = d.ticker
        where d.instrumento_canonico
          and d.corrente
          and d.isin_valido
          and (
              a.ticker is null
              or upper(trim(a.isin)) = d.isin
          )
    ) as validados_b3,
    (
        select count(*)
        from investimento.ativos a
        join tmp_b3_decisions d
          on d.ticker = upper(trim(a.ticker))
        where d.isin_valido
          and a.isin is not null
          and trim(a.isin) <> ''
          and upper(trim(a.isin)) <> d.isin
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
              select 1
              from tmp_b3_decisions d
              where d.ticker = upper(trim(a.ticker))
          )
    ) as inativos
"""


UPDATE_CANONICAL_SQL = """
update investimento.ativos a
   set nome = coalesce(d.nome, a.nome),
       nome_pregao = coalesce(d.nome_pregao, a.nome_pregao),
       classe = case
           when a.status_validacao = 'VALIDADO_OFICIAL' then a.classe
           else d.classe_preliminar
       end,
       subclasse = case
           when a.status_validacao = 'VALIDADO_OFICIAL' then a.subclasse
           else d.subclasse_preliminar
       end,
       tipo_instrumento = d.categoria_b3,
       categoria_b3 = d.categoria_b3,
       segmento_b3 = d.segmento_b3,
       mercado_b3 = d.mercado_b3,
       moeda = coalesce(d.moeda, a.moeda),
       isin = d.isin,
       ativo = case
           when a.status_validacao = 'VALIDADO_OFICIAL' then a.ativo
           else d.corrente
       end,
       instrumento_canonico = true,
       tipo_variante_b3 = null,
       ticker_canonico = d.ticker,
       elegivel_analise = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then a.elegivel_analise
           else false
       end,
       status_validacao = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then 'VALIDADO_OFICIAL'
           when d.corrente then 'VALIDADO_B3'
           when d.motivo_validacao_b3 = 'INATIVO_B3' then 'INATIVO'
           else 'DUVIDOSO'
       end,
       motivo_exclusao = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then a.motivo_exclusao
           when d.corrente
               then 'Canônico na B3; aguarda validações oficiais complementares obrigatórias da classe.'
           else concat(
               d.motivo_validacao_b3,
               '; instrumento estruturalmente canônico; atividade aguarda confirmação oficial complementar (COTAHIST/B3, CVM ou fonte oficial da classe).'
           )
       end,
       fonte_validacao = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then a.fonte_validacao
           else %(source_code)s
       end,
       validado_em = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then a.validado_em
           else now()
       end,
       atualizado_em = now()
  from tmp_b3_decisions d
 where d.ticker = upper(trim(a.ticker))
   and d.instrumento_canonico
   and d.isin_valido
   and a.isin is not null
   and upper(trim(a.isin)) = d.isin
"""


UPDATE_NON_CANONICAL_SQL = """
update investimento.ativos a
   set nome = coalesce(d.nome, a.nome),
       nome_pregao = coalesce(d.nome_pregao, a.nome_pregao),
       classe = d.classe_preliminar,
       subclasse = d.subclasse_preliminar,
       tipo_instrumento = d.categoria_b3,
       categoria_b3 = d.categoria_b3,
       segmento_b3 = d.segmento_b3,
       mercado_b3 = d.mercado_b3,
       moeda = coalesce(d.moeda, a.moeda),
       ativo = false,
       instrumento_canonico = false,
       tipo_variante_b3 = d.tipo_variante_b3,
       ticker_canonico = d.ticker_canonico,
       elegivel_analise = false,
       status_validacao = 'NAO_CANONICO',
       motivo_exclusao = concat(
           'Instrumento B3 não canônico: ',
           d.tipo_variante_b3,
           '.'
       ),
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
  from tmp_b3_decisions d
 where d.ticker = upper(trim(a.ticker))
   and not d.instrumento_canonico
   and d.tipo_variante_b3 in (
       'EQUITY_BLOCK_TRADING_LOT',
       'ETF_PRIMARY_MARKET',
       'ODD_LOT',
       'RIGHTS',
       'RECEIPTS',
       'WARRANT',
       'INDEX'
   )
   and d.isin_valido
   and a.isin is not null
   and upper(trim(a.isin)) = d.isin
"""


UPDATE_DIVERGENT_SQL = """
update investimento.ativos a
   set ativo = false,
       instrumento_canonico = d.instrumento_canonico,
       tipo_variante_b3 = d.tipo_variante_b3,
       ticker_canonico = d.ticker_canonico,
       elegivel_analise = false,
       status_validacao = 'DIVERGENTE',
       motivo_exclusao = 'Ticker encontrado na B3 com ISIN diferente do cadastro mestre.',
       categoria_b3 = d.categoria_b3,
       segmento_b3 = d.segmento_b3,
       mercado_b3 = d.mercado_b3,
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 from tmp_b3_decisions d
 where d.ticker = upper(trim(a.ticker))
   and d.isin_valido
   and a.isin is not null
   and trim(a.isin) <> ''
   and upper(trim(a.isin)) <> d.isin
"""


UPDATE_INVALID_OFFICIAL_ID_SQL = """
update investimento.ativos a
   set ativo = false,
       instrumento_canonico = false,
       tipo_variante_b3 = null,
       ticker_canonico = null,
       elegivel_analise = false,
       status_validacao = 'DUVIDOSO',
       motivo_exclusao = 'Ticker encontrado na B3 sem ISIN oficial válido.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 from tmp_b3_decisions d
 where d.ticker = upper(trim(a.ticker))
   and not d.isin_valido
"""


UPDATE_WITHOUT_ISIN_SQL = """
update investimento.ativos
   set ativo = false,
       instrumento_canonico = false,
       tipo_variante_b3 = null,
       ticker_canonico = null,
       elegivel_analise = false,
       status_validacao = 'SEM_ISIN',
       motivo_exclusao = 'Ativo sem ISIN válido no cadastro mestre.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 where isin is null or trim(isin) = ''
"""


UPDATE_INACTIVE_SQL = """
update investimento.ativos a
   set ativo = false,
       elegivel_analise = false,
       status_validacao = 'INATIVO',
       motivo_exclusao = 'Ticker não encontrado como instrumento corrente no último cadastro oficial da B3.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atualizado_em = now()
 where a.isin is not null
   and trim(a.isin) <> ''
   and not exists (
       select 1
       from tmp_b3_decisions d
       where d.ticker = upper(trim(a.ticker))
   )
"""


INSERT_NEW_SQL = """
insert into investimento.ativos (
    ticker, nome, nome_pregao, classe, subclasse, tipo_instrumento,
    moeda, ativo, isin, fonte_cadastro, url_fonte, categoria_b3,
    segmento_b3, mercado_b3, instrumento_canonico, tipo_variante_b3,
    ticker_canonico, status_validacao, elegivel_analise,
    motivo_exclusao, fonte_validacao, validado_em, atualizado_em
)
select
    d.ticker,
    d.nome,
    d.nome_pregao,
    d.classe_preliminar,
    d.subclasse_preliminar,
    d.categoria_b3,
    coalesce(d.moeda, 'BRL'),
    d.instrumento_canonico and d.corrente,
    d.isin,
    %(source_code)s,
    %(source_url)s,
    d.categoria_b3,
    d.segmento_b3,
    d.mercado_b3,
    d.instrumento_canonico,
    d.tipo_variante_b3,
    d.ticker_canonico,
    case
        when d.instrumento_canonico and d.corrente then 'VALIDADO_B3'
        when d.instrumento_canonico
             and d.motivo_validacao_b3 = 'INATIVO_B3' then 'INATIVO'
        when d.instrumento_canonico then 'DUVIDOSO'
        else 'NAO_CANONICO'
    end,
    false,
    case
        when d.instrumento_canonico and d.corrente
            then 'Canônico na B3; aguarda validações oficiais complementares obrigatórias da classe.'
        when d.instrumento_canonico
            then concat(
                d.motivo_validacao_b3,
                '; instrumento estruturalmente canônico; atividade aguarda confirmação oficial complementar (COTAHIST/B3, CVM ou fonte oficial da classe).'
            )
        else concat(
            'Instrumento B3 não canônico: ',
            d.tipo_variante_b3,
            '.'
        )
    end,
    %(source_code)s,
    now(),
    now()
from tmp_b3_decisions d
where d.em_escopo_mestre
  and not exists (
      select 1
      from investimento.ativos a
      where upper(trim(a.ticker)) = d.ticker
  )
on conflict (ticker) do nothing
"""


def validate_master(conn):
    progress("aplicando universo canônico ao cadastro mestre em operações set-based")
    with conn.cursor() as cur:
        cur.execute(COUNTS_SQL)
        columns = [description.name for description in cur.description]
        counts = dict(zip(columns, cur.fetchone()))
        params = {
            "source_code": SOURCE_CODE,
            "source_url": SOURCE_URL,
        }
        cur.execute(UPDATE_CANONICAL_SQL, params)
        cur.execute(UPDATE_NON_CANONICAL_SQL, params)
        cur.execute(UPDATE_DIVERGENT_SQL, params)
        cur.execute(UPDATE_INVALID_OFFICIAL_ID_SQL, params)
        cur.execute(UPDATE_WITHOUT_ISIN_SQL, params)
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
                (result or {}).get("total_bruto_snapshot"),
                (result or {}).get("snapshot", 0),
                message[:1500],
                log_id,
            ),
        )
    conn.commit()


def main():
    progress("iniciando validação e construção do universo canônico")
    conn = connect()
    log_id = log_start(conn)
    try:
        ref, status, raw = download_latest()
        progress("normalizando arquivo oficial completo")
        instruments = [normalize(row, ref, status) for row in raw]
        del raw
        annotate_universe(instruments, ref)
        audit = build_audit(instruments)
        progress(
            "normalização concluída: "
            f"linhas={len(instruments)} "
            f"canonicos={audit['total_canonico']} "
            f"nao_canonicos={audit['total_nao_canonico']}"
        )

        snapshot_count = save_snapshot(conn, instruments, ref)
        counts = validate_master(conn)
        conn.commit()

        result = {
            "data_referencia": str(ref),
            "status_arquivo": status,
            "snapshot": snapshot_count,
            "total_bruto_snapshot": audit["total_bruto_snapshot"],
            "total_canonico": audit["total_canonico"],
            "total_nao_canonico": audit["total_nao_canonico"],
            "distribuicao_canonica_classe": audit[
                "distribuicao_canonica_classe"
            ],
            "total_canonicos_confirmados_b3": audit[
                "total_canonicos_confirmados_b3"
            ],
            "total_canonicos_pendentes_data_inicio": audit[
                "total_canonicos_pendentes_data_inicio"
            ],
            "distribuicao_pendentes_data_inicio_classe": audit[
                "distribuicao_pendentes_data_inicio_classe"
            ],
            "etfs_canonicos_renda_variavel": audit[
                "etfs_canonicos_renda_variavel"
            ],
            "etfs_canonicos_renda_fixa": audit[
                "etfs_canonicos_renda_fixa"
            ],
            "etfs_canonicos_total": audit["etfs_canonicos_total"],
            "distribuicao_nao_canonicos_motivo": audit[
                "distribuicao_nao_canonicos_motivo"
            ],
            **counts,
        }
        message = json.dumps(result, ensure_ascii=False, sort_keys=True)
        log_end(conn, log_id, "SUCESSO", result, message)
        emit_audit(audit)
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
