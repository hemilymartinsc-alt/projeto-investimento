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

# Thresholds iniciais conservadores/fail-closed. Devem ser recalibrados apenas
# com histórico real, sem substituir as comparações contra o último snapshot.
MIN_SNAPSHOT_RECORDS = 100_000
MAX_SNAPSHOT_VARIATION_RATIO = 0.15
MAX_CANONICAL_VARIATION_RATIO = 0.20
MAX_CONFIRMED_CANONICAL_VARIATION_RATIO = 0.15
MAX_CONFIRMED_CLASS_VARIATION_RATIO = 0.25
ESSENTIAL_B3_CATEGORIES = frozenset(
    {"SHARES", "UNIT", "BDR", "FUNDS", "ETF EQUITIES"}
)
SANITY_CANONICAL_CLASSES = ("ACAO", "BDR", "ETF", "FUNDO")

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
    "atividade_confirmada_b3",
    "status_atividade_b3",
    "motivo_atividade_b3",
    "isin_valido",
    "em_escopo_mestre",
)


class SnapshotSanityError(RuntimeError):
    """Impede que uma coleta B3 inválida altere o estado vigente."""


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


def activity_state(inst, ref):
    issue = activity_validation_issue(inst, ref)
    if issue is None:
        return True, "CONFIRMADA", None
    if issue == "DATA_INICIO_NAO_INFORMADA_B3":
        return False, "PENDENTE_DATA_INICIO", issue
    if issue == "INICIO_NEGOCIACAO_FUTURO":
        return False, "INICIO_FUTURO", issue
    if issue == "INATIVO_B3":
        return False, "INATIVA_B3", issue
    raise ValueError(f"Situação de atividade B3 desconhecida: {issue}")


def snapshot_profile(instruments, ref=None):
    categories = set()
    total_canonical = 0
    total_confirmed = 0
    confirmed_classes = Counter()

    for inst in instruments:
        category = norm(inst.get("categoria_b3"))
        if category:
            categories.add(category)
        if "instrumento_canonico" not in inst:
            raise SnapshotSanityError(
                "perfil canônico não calculado antes da sanidade"
            )
        if not inst["instrumento_canonico"]:
            continue
        total_canonical += 1
        if inst.get("atividade_confirmada_b3"):
            total_confirmed += 1
            classe = inst.get("classe_preliminar")
            if classe in SANITY_CANONICAL_CLASSES:
                confirmed_classes[classe] += 1

    return {
        "data_referencia": str(ref) if ref is not None else None,
        "total_registros": len(instruments),
        "categorias": sorted(categories),
        "total_canonicos": total_canonical,
        "total_canonicos_confirmados": total_confirmed,
        "canonicos_confirmados_por_classe": {
            classe: confirmed_classes[classe]
            for classe in SANITY_CANONICAL_CLASSES
        },
    }


def check_sanity_variation(
    errors,
    variations,
    rule,
    current_value,
    previous_value,
    maximum_ratio,
):
    if previous_value is None:
        return
    if previous_value <= 0:
        if current_value != previous_value:
            errors.append(
                f"sanidade[{rule}] anterior={previous_value} "
                f"atual={current_value} variação=indeterminada "
                f"limite={maximum_ratio:.2%}"
            )
        return

    variation_ratio = abs(current_value - previous_value) / previous_value
    variations[rule] = variation_ratio
    if variation_ratio > maximum_ratio:
        errors.append(
            f"sanidade[{rule}] anterior={previous_value} "
            f"atual={current_value} variação={variation_ratio:.2%} "
            f"limite={maximum_ratio:.2%}"
        )


def validate_snapshot_sanity(
    instruments, status, previous_profile=None, ref=None
):
    profile = snapshot_profile(instruments, ref)
    errors = []

    if status != "Final":
        errors.append(f"status_arquivo={status!r}; esperado='Final'")

    if profile["total_registros"] < MIN_SNAPSHOT_RECORDS:
        errors.append(
            "volume abaixo do mínimo absoluto: "
            f"{profile['total_registros']} < {MIN_SNAPSHOT_RECORDS}"
        )

    missing_categories = sorted(
        ESSENTIAL_B3_CATEGORIES - set(profile["categorias"])
    )
    if missing_categories:
        errors.append(
            "categorias essenciais ausentes: " + ", ".join(missing_categories)
        )

    variations = {}
    if previous_profile is not None:
        check_sanity_variation(
            errors,
            variations,
            "snapshot_bruto",
            profile["total_registros"],
            previous_profile.get("total_registros"),
            MAX_SNAPSHOT_VARIATION_RATIO,
        )
        check_sanity_variation(
            errors,
            variations,
            "total_canonicos",
            profile["total_canonicos"],
            previous_profile.get("total_canonicos"),
            MAX_CANONICAL_VARIATION_RATIO,
        )
        check_sanity_variation(
            errors,
            variations,
            "canonicos_confirmados",
            profile["total_canonicos_confirmados"],
            previous_profile.get("total_canonicos_confirmados"),
            MAX_CONFIRMED_CANONICAL_VARIATION_RATIO,
        )
        previous_classes = previous_profile.get(
            "canonicos_confirmados_por_classe", {}
        )
        for classe in SANITY_CANONICAL_CLASSES:
            check_sanity_variation(
                errors,
                variations,
                f"canonicos_confirmados_classe_{classe}",
                profile["canonicos_confirmados_por_classe"][classe],
                previous_classes.get(classe),
                MAX_CONFIRMED_CLASS_VARIATION_RATIO,
            )

    result = {
        **profile,
        "snapshot_anterior": previous_profile,
        "variacoes_percentuais_absolutas": variations,
        "valido": not errors,
        "erros": errors,
    }
    if errors:
        raise SnapshotSanityError("; ".join(errors))
    return result


def load_latest_valid_snapshot_profile(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            with ultima_referencia as (
                select max(data_referencia) as data_referencia
                from investimento.b3_instrumentos_snapshot
                where status_arquivo = 'Final'
            ), snapshot_atual as (
                select
                    s.*,
                    (
                        s.instrumento_canonico = true
                        and nullif(
                            s.data_inicio_negociacao,
                            date '9999-12-31'
                        ) is not null
                        and nullif(
                            s.data_inicio_negociacao,
                            date '9999-12-31'
                        ) <= s.data_referencia
                        and (
                            nullif(
                                s.data_fim_negociacao,
                                date '9999-12-31'
                            ) is null
                            or nullif(
                                s.data_fim_negociacao,
                                date '9999-12-31'
                            ) >= s.data_referencia
                        )
                        and (
                            nullif(
                                s.data_expiracao,
                                date '9999-12-31'
                            ) is null
                            or nullif(
                                s.data_expiracao,
                                date '9999-12-31'
                            ) >= s.data_referencia
                        )
                    ) as atividade_confirmada,
                    case
                        when upper(trim(s.categoria_b3)) in (
                            'SHARES', 'UNIT'
                        ) then 'ACAO'
                        when upper(trim(s.categoria_b3)) = 'BDR'
                            then 'BDR'
                        when upper(trim(s.categoria_b3)) like 'ETF%'
                          or upper(trim(s.categoria_b3)) =
                             'FIXED INCOME TRADABLE INSTRUMENT T1'
                            then 'ETF'
                        when upper(trim(s.categoria_b3)) = 'FUNDS'
                            then 'FUNDO'
                        else 'OUTRO'
                    end as classe_preliminar
                from investimento.b3_instrumentos_snapshot s
                join ultima_referencia u
                  on u.data_referencia = s.data_referencia
                where s.status_arquivo = 'Final'
            )
            select
                u.data_referencia,
                count(s.ticker) as total_registros,
                coalesce(
                    array_agg(
                        distinct upper(trim(s.categoria_b3))
                        order by upper(trim(s.categoria_b3))
                    ) filter (where s.categoria_b3 is not null),
                    array[]::text[]
                ) as categorias,
                count(*) filter (
                    where s.instrumento_canonico = true
                ) as total_canonicos,
                count(*) filter (
                    where s.atividade_confirmada = true
                ) as total_canonicos_confirmados,
                count(*) filter (
                    where s.atividade_confirmada = true
                      and s.classe_preliminar = 'ACAO'
                ) as acoes_confirmadas,
                count(*) filter (
                    where s.atividade_confirmada = true
                      and s.classe_preliminar = 'BDR'
                ) as bdrs_confirmados,
                count(*) filter (
                    where s.atividade_confirmada = true
                      and s.classe_preliminar = 'ETF'
                ) as etfs_confirmados,
                count(*) filter (
                    where s.atividade_confirmada = true
                      and s.classe_preliminar = 'FUNDO'
                ) as fundos_confirmados
            from ultima_referencia u
            left join snapshot_atual s on true
            group by u.data_referencia
            """
        )
        row = cur.fetchone()

    if not row or row[0] is None:
        return None
    return {
        "data_referencia": str(row[0]),
        "total_registros": row[1],
        "categorias": list(row[2]),
        "total_canonicos": row[3],
        "total_canonicos_confirmados": row[4],
        "canonicos_confirmados_por_classe": {
            "ACAO": row[5],
            "BDR": row[6],
            "ETF": row[7],
            "FUNDO": row[8],
        },
    }


def valid_isin(inst):
    isin = inst.get("isin")
    return bool(isin and ISIN_RE.fullmatch(isin))


def new_master_candidate(inst):
    return bool(
        inst.get("instrumento_canonico")
        and inst.get("atividade_confirmada_b3")
        and inst.get("isin_valido")
    )


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
        (
            activity_confirmed,
            activity_status,
            activity_reason,
        ) = activity_state(inst, ref)
        inst["corrente"] = activity_confirmed
        inst["motivo_validacao_b3"] = (
            activity_reason if is_canonical else None
        )
        inst["atividade_confirmada_b3"] = activity_confirmed
        inst["status_atividade_b3"] = activity_status
        inst["motivo_atividade_b3"] = activity_reason
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

        # Somente instrumentos que já tiveram atividade soberana confirmada
        # pela B3 podem nascer no cadastro mestre. O snapshot segue integral.
        inst["em_escopo_mestre"] = new_master_candidate(inst)

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
        inst["atividade_confirmada_b3"],
        inst["status_atividade_b3"],
        inst.get("motivo_atividade_b3"),
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
                atividade_confirmada_b3 boolean not null,
                status_atividade_b3 text not null,
                motivo_atividade_b3 text,
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
                data_referencia,
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
                atividade_confirmada_b3,
                status_atividade_b3,
                motivo_atividade_b3,
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
    canonical_activity_statuses = Counter()
    canonical_etfs = Counter()
    non_canonical_reasons = Counter()
    examples = defaultdict(list)
    requested = {ticker: None for ticker in AUDIT_TICKERS}

    for inst in instruments:
        if inst["instrumento_canonico"]:
            canonical_classes[inst["classe_preliminar"]] += 1
            canonical_activity_statuses[inst["status_atividade_b3"]] += 1
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
                "atividade_confirmada_b3": inst[
                    "atividade_confirmada_b3"
                ],
                "status_atividade_b3": inst["status_atividade_b3"],
                "motivo_atividade_b3": inst.get("motivo_atividade_b3"),
                "status_validacao_proposto": (
                    "VALIDADO_B3"
                    if inst["instrumento_canonico"]
                    else "NAO_CANONICO"
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
    total_master_candidates = sum(
        1 for inst in instruments if new_master_candidate(inst)
    )
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
        "total_candidatos_novos_mestre": total_master_candidates,
        "total_somente_snapshot_b3": len(instruments) - total_master_candidates,
        "distribuicao_status_atividade_canonicos": dict(
            canonical_activity_statuses.most_common()
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
        "AUDITORIA | destino_cadastro "
        f"candidatos_novos_mestre="
        f"{audit['total_candidatos_novos_mestre']} "
        f"somente_snapshot_b3={audit['total_somente_snapshot_b3']}"
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
        "AUDITORIA | status_atividade_canonicos="
        + json.dumps(
            audit["distribuicao_status_atividade_canonicos"],
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
          and d.atividade_confirmada_b3
          and d.isin_valido
    ) as canonicos_confirmados_b3,
    (
        select count(*)
        from tmp_b3_decisions d
        where d.instrumento_canonico
          and d.status_atividade_b3 = 'PENDENTE_DATA_INICIO'
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
        where d.instrumento_canonico
          and d.atividade_confirmada_b3
          and d.isin_valido
          and not exists (
              select 1
              from investimento.ativos a
              where upper(trim(a.ticker)) = d.ticker
          )
    ) as novos_canonicos,
    (
        select count(*)
        from tmp_b3_decisions d
        left join investimento.ativos a
          on upper(trim(a.ticker)) = d.ticker
        where d.instrumento_canonico
          and d.atividade_confirmada_b3
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
        where not exists (
              select 1
              from tmp_b3_decisions d
              where d.ticker = upper(trim(a.ticker))
          )
    ) as ausentes_b3_atual
"""


PREPARE_PREVIOUS_STATE_SQL = """
create temporary table tmp_b3_previous
on commit drop
as
select distinct on (upper(trim(ticker)))
    upper(trim(ticker)) as ticker,
    atividade_confirmada_b3,
    status_atividade_b3,
    elegivel_analise,
    instrumento_canonico,
    nome,
    nome_pregao,
    tipo_instrumento,
    categoria_b3,
    segmento_b3,
    mercado_b3,
    moeda,
    ticker_canonico
from investimento.ativos
order by upper(trim(ticker)), atualizado_em desc nulls last, id desc
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
       ativo = d.atividade_confirmada_b3,
       instrumento_canonico = true,
       tipo_variante_b3 = null,
       ticker_canonico = d.ticker,
       elegivel_analise = case
           when a.elegivel_analise = true
            and a.status_validacao = 'VALIDADO_OFICIAL'
            and d.atividade_confirmada_b3 = true then true
           else false
       end,
       status_validacao = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then 'VALIDADO_OFICIAL'
           else 'VALIDADO_B3'
       end,
       motivo_exclusao = case
           when a.status_validacao = 'VALIDADO_OFICIAL'
               then a.motivo_exclusao
           else 'Canônico na B3; aguarda validações oficiais complementares obrigatórias da classe.'
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
       atividade_confirmada_b3 = d.atividade_confirmada_b3,
       status_atividade_b3 = d.status_atividade_b3,
       motivo_atividade_b3 = d.motivo_atividade_b3,
       data_referencia_b3 = d.data_referencia,
       ultima_confirmacao_b3 = case
           when d.atividade_confirmada_b3 then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
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
       atividade_confirmada_b3 = d.atividade_confirmada_b3,
       status_atividade_b3 = d.status_atividade_b3,
       motivo_atividade_b3 = d.motivo_atividade_b3,
       data_referencia_b3 = d.data_referencia,
       ultima_confirmacao_b3 = case
           when d.atividade_confirmada_b3 then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
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
   set nome = coalesce(d.nome, a.nome),
       nome_pregao = coalesce(d.nome_pregao, a.nome_pregao),
       tipo_instrumento = d.categoria_b3,
       moeda = coalesce(d.moeda, a.moeda),
       ativo = d.atividade_confirmada_b3,
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
       atividade_confirmada_b3 = d.atividade_confirmada_b3,
       status_atividade_b3 = d.status_atividade_b3,
       motivo_atividade_b3 = d.motivo_atividade_b3,
       data_referencia_b3 = d.data_referencia,
       ultima_confirmacao_b3 = case
           when d.atividade_confirmada_b3 then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
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
   set nome = coalesce(d.nome, a.nome),
       nome_pregao = coalesce(d.nome_pregao, a.nome_pregao),
       tipo_instrumento = d.categoria_b3,
       categoria_b3 = d.categoria_b3,
       segmento_b3 = d.segmento_b3,
       mercado_b3 = d.mercado_b3,
       moeda = coalesce(d.moeda, a.moeda),
       ativo = false,
       instrumento_canonico = false,
       tipo_variante_b3 = null,
       ticker_canonico = null,
       elegivel_analise = false,
       status_validacao = 'DUVIDOSO',
       motivo_exclusao = 'Ticker encontrado na B3 sem ISIN oficial válido.',
       fonte_validacao = %(source_code)s,
       validado_em = now(),
       atividade_confirmada_b3 = d.atividade_confirmada_b3,
       status_atividade_b3 = d.status_atividade_b3,
       motivo_atividade_b3 = d.motivo_atividade_b3,
       data_referencia_b3 = d.data_referencia,
       ultima_confirmacao_b3 = case
           when d.atividade_confirmada_b3 then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
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


UPDATE_PRESENT_WITHOUT_ISIN_SQL = """
update investimento.ativos a
   set nome = coalesce(d.nome, a.nome),
       nome_pregao = coalesce(d.nome_pregao, a.nome_pregao),
       tipo_instrumento = d.categoria_b3,
       categoria_b3 = d.categoria_b3,
       segmento_b3 = d.segmento_b3,
       mercado_b3 = d.mercado_b3,
       moeda = coalesce(d.moeda, a.moeda),
       atividade_confirmada_b3 = d.atividade_confirmada_b3,
       status_atividade_b3 = d.status_atividade_b3,
       motivo_atividade_b3 = d.motivo_atividade_b3,
       data_referencia_b3 = d.data_referencia,
       ultima_confirmacao_b3 = case
           when d.atividade_confirmada_b3 then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
       atualizado_em = now()
  from tmp_b3_decisions d
 where d.ticker = upper(trim(a.ticker))
   and (a.isin is null or trim(a.isin) = '')
"""


UPDATE_PRESENT_B3_STATE_SQL = """
update investimento.ativos a
   set nome = coalesce(d.nome, a.nome),
       nome_pregao = coalesce(d.nome_pregao, a.nome_pregao),
       tipo_instrumento = d.categoria_b3,
       categoria_b3 = d.categoria_b3,
       segmento_b3 = d.segmento_b3,
       mercado_b3 = d.mercado_b3,
       moeda = coalesce(d.moeda, a.moeda),
       ticker_canonico = d.ticker_canonico,
       elegivel_analise = case
           when a.elegivel_analise = true
            and a.instrumento_canonico = true
            and a.status_validacao = 'VALIDADO_OFICIAL'
            and d.atividade_confirmada_b3 = true then true
           else false
       end,
       atividade_confirmada_b3 = d.atividade_confirmada_b3,
       status_atividade_b3 = d.status_atividade_b3,
       motivo_atividade_b3 = d.motivo_atividade_b3,
       data_referencia_b3 = d.data_referencia,
       ultima_confirmacao_b3 = case
           when d.atividade_confirmada_b3 then now()
           else a.ultima_confirmacao_b3
       end,
       verificado_b3_em = now(),
       atualizado_em = now()
  from tmp_b3_decisions d
 where d.ticker = upper(trim(a.ticker))
"""


UPDATE_ABSENT_SQL = """
update investimento.ativos a
   set ativo = false,
       elegivel_analise = false,
       atividade_confirmada_b3 = false,
       status_atividade_b3 = 'AUSENTE_B3_ATUAL',
       motivo_atividade_b3 = 'Ticker ausente do último snapshot oficial válido da B3.',
       data_referencia_b3 = (
           select max(data_referencia) from tmp_b3_decisions
       ),
       verificado_b3_em = now(),
       atualizado_em = now()
 where not exists (
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
    motivo_exclusao, fonte_validacao, validado_em,
    atividade_confirmada_b3, status_atividade_b3, motivo_atividade_b3,
    data_referencia_b3, ultima_confirmacao_b3, verificado_b3_em,
    atualizado_em
)
select
    d.ticker,
    d.nome,
    d.nome_pregao,
    d.classe_preliminar,
    d.subclasse_preliminar,
    d.categoria_b3,
    coalesce(d.moeda, 'BRL'),
    true,
    d.isin,
    %(source_code)s,
    %(source_url)s,
    d.categoria_b3,
    d.segmento_b3,
    d.mercado_b3,
    true,
    null,
    d.ticker,
    'VALIDADO_B3',
    false,
    'Canônico na B3; aguarda validações oficiais complementares obrigatórias da classe.',
    %(source_code)s,
    now(),
    true,
    'CONFIRMADA',
    null,
    d.data_referencia,
    now(),
    now(),
    now()
from tmp_b3_decisions d
where d.instrumento_canonico = true
  and d.atividade_confirmada_b3 = true
  and d.isin_valido = true
  and not exists (
      select 1
      from investimento.ativos a
      where upper(trim(a.ticker)) = d.ticker
  )
on conflict (ticker) do nothing
"""


TRANSITIONS_SQL = """
select
    count(*) filter (
        where p.ticker is not null
          and p.atividade_confirmada_b3 = false
          and a.atividade_confirmada_b3 = true
    ) as atividade_false_para_true,
    count(*) filter (
        where p.ticker is not null
          and p.atividade_confirmada_b3 = true
          and a.atividade_confirmada_b3 = false
    ) as atividade_true_para_false,
    count(*) filter (
        where p.ticker is null
          and a.instrumento_canonico = true
          and a.atividade_confirmada_b3 = true
    ) as novos_tickers_canonicos_ativos,
    count(*) filter (
        where a.status_atividade_b3 = 'AUSENTE_B3_ATUAL'
          and p.status_atividade_b3 is distinct from 'AUSENTE_B3_ATUAL'
    ) as agora_ausentes_b3,
    count(*) filter (
        where a.status_atividade_b3 = 'INATIVA_B3'
          and p.status_atividade_b3 is distinct from 'INATIVA_B3'
    ) as agora_inativos_b3,
    count(*) filter (
        where p.ticker is not null
          and (
              a.nome is distinct from p.nome
              or a.nome_pregao is distinct from p.nome_pregao
              or a.tipo_instrumento is distinct from p.tipo_instrumento
              or a.categoria_b3 is distinct from p.categoria_b3
              or a.segmento_b3 is distinct from p.segmento_b3
              or a.mercado_b3 is distinct from p.mercado_b3
              or a.moeda is distinct from p.moeda
              or a.ticker_canonico is distinct from p.ticker_canonico
          )
    ) as alteracoes_campos_b3
from investimento.ativos a
left join tmp_b3_previous p
  on p.ticker = upper(trim(a.ticker))
where a.data_referencia_b3 = (
    select max(data_referencia) from tmp_b3_decisions
)
"""


def validate_master(conn):
    progress("aplicando universo canônico ao cadastro mestre em operações set-based")
    with conn.cursor() as cur:
        cur.execute(PREPARE_PREVIOUS_STATE_SQL)
        cur.execute("create unique index on tmp_b3_previous (ticker)")
        cur.execute("analyze tmp_b3_previous")
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
        cur.execute(UPDATE_PRESENT_WITHOUT_ISIN_SQL, params)
        cur.execute(UPDATE_PRESENT_B3_STATE_SQL, params)
        cur.execute(UPDATE_ABSENT_SQL, params)
        cur.execute(INSERT_NEW_SQL, params)
        cur.execute(TRANSITIONS_SQL)
        transition_columns = [
            description.name for description in cur.description
        ]
        transitions = dict(zip(transition_columns, cur.fetchone()))

    counts.update(transitions)

    progress(
        "cadastro mestre atualizado: "
        + " ".join(f"{key}={value}" for key, value in counts.items())
    )
    return counts


def process_snapshot(conn, instruments, ref, status):
    """Valida integralmente antes da primeira mutação e grava em uma transação."""
    previous_profile = load_latest_valid_snapshot_profile(conn)
    annotate_universe(instruments, ref)
    sanity = validate_snapshot_sanity(
        instruments,
        status,
        previous_profile=previous_profile,
        ref=ref,
    )
    progress(
        "sanidade aprovada antes de qualquer mutação: "
        f"status={status} linhas={len(instruments)} "
        f"referencia_anterior="
        f"{(previous_profile or {}).get('data_referencia')} "
        "variacoes="
        + json.dumps(
            sanity["variacoes_percentuais_absolutas"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )

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
    return sanity, audit, snapshot_count, counts


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
        sanity, audit, snapshot_count, counts = process_snapshot(
            conn, instruments, ref, status
        )

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
            "total_candidatos_novos_mestre": audit[
                "total_candidatos_novos_mestre"
            ],
            "total_somente_snapshot_b3": audit[
                "total_somente_snapshot_b3"
            ],
            "distribuicao_pendentes_data_inicio_classe": audit[
                "distribuicao_pendentes_data_inicio_classe"
            ],
            "distribuicao_status_atividade_canonicos": audit[
                "distribuicao_status_atividade_canonicos"
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
            "sanidade_snapshot": sanity,
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
