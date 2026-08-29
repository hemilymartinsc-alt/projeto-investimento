from __future__ import annotations

import base64
import json
import re
import time
from datetime import date, datetime
from typing import Any, Iterable

import requests


BASE_URL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "listedCompaniesProxy/CompanyCall"
)
SOURCE_CODE = "B3_LISTED_COMPANIES"
PROCESS_NAME = "coletar_catalogo_empresas_listadas_b3"

TICKER_RE = re.compile(r"^[A-Z]{4}[0-9]{1,2}$")
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _encode_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.b64encode(raw).decode()


def _get_json(session: requests.Session, endpoint: str, payload: dict[str, Any]) -> Any:
    token = _encode_payload(payload)
    url = f"{BASE_URL}/{endpoint}/{token}"
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


def _parse_date(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue

    return None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def fetch_all_companies(session: requests.Session) -> list[dict[str, Any]]:
    page = 1
    page_size = 120
    companies: list[dict[str, Any]] = []
    seen: set[str] = set()

    while page <= 100:
        payload = {
            "language": "pt-br",
            "pageNumber": page,
            "pageSize": page_size,
        }
        data = _get_json(session, "GetInitialCompanies", payload)

        if not isinstance(data, dict):
            raise RuntimeError("Resposta inesperada da B3 em GetInitialCompanies.")

        results = data.get("results") or []
        if not isinstance(results, list):
            raise RuntimeError("Campo 'results' da B3 não é uma lista.")

        new_in_page = 0
        for company in results:
            if not isinstance(company, dict):
                continue
            code_cvm = _clean(
                _first(company, "codeCVM", "codeCvm", "codigoCVM")
            )
            if not code_cvm or code_cvm in seen:
                continue
            seen.add(code_cvm)
            companies.append(company)
            new_in_page += 1

        if len(results) < page_size or new_in_page == 0:
            break

        page += 1

    return companies


def fetch_company_detail(
    session: requests.Session,
    code_cvm: str,
) -> dict[str, Any]:
    data = _get_json(
        session,
        "GetDetail",
        {"codeCVM": code_cvm, "language": "pt-br"},
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"Detalhe B3 inválido para código CVM {code_cvm}.")
    return data


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def extract_tickers(detail: dict[str, Any]) -> list[tuple[str, str | None, bool]]:
    """
    Extrai códigos de negociação e ISIN do JSON da B3 de forma tolerante
    a pequenas mudanças de nomes de campos.

    A validação definitiva de que o ticker é uma ação negociada será feita
    depois contra o arquivo oficial de instrumentos/COTAHIST da B3.
    """
    found: dict[str, dict[str, Any]] = {}

    main_code = _clean(
        _first(
            detail,
            "tradingCode",
            "tradingCodes",
            "code",
            "ticker",
        )
    )
    if main_code:
        main_code = main_code.upper()

    for node in _walk_dicts(detail):
        strings: list[str] = []
        for value in node.values():
            if isinstance(value, str):
                strings.append(value.strip().upper())

        tickers = [s for s in strings if TICKER_RE.fullmatch(s)]
        isins = [s for s in strings if ISIN_RE.fullmatch(s)]

        for ticker in tickers:
            rec = found.setdefault(
                ticker,
                {"isin": None, "principal": ticker == main_code},
            )
            if isins and not rec["isin"]:
                rec["isin"] = isins[0]

    # Segunda passada: captura ticker que apareça isolado em qualquer campo.
    def walk_values(value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            for child in value.values():
                yield from walk_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk_values(child)
        elif isinstance(value, str):
            yield value.strip().upper()

    for text in walk_values(detail):
        if TICKER_RE.fullmatch(text):
            found.setdefault(
                text,
                {"isin": None, "principal": text == main_code},
            )

    return [
        (ticker, data["isin"], bool(data["principal"]))
        for ticker, data in sorted(found.items())
    ]


def normalize_company(
    initial: dict[str, Any],
    detail: dict[str, Any],
) -> tuple:
    merged = {**initial, **detail}

    code_cvm = _clean(
        _first(merged, "codeCVM", "codeCvm", "codigoCVM")
    )
    if not code_cvm:
        raise RuntimeError("Companhia B3 sem código CVM.")

    return (
        code_cvm,
        _clean(_first(merged, "cnpj", "CNPJ")),
        _clean(_first(merged, "companyName", "company")),
        _clean(_first(merged, "tradingName")),
        _clean(_first(merged, "issuingCompany")),
        _clean(_first(merged, "segment")),
        _clean(_first(merged, "market")),
        _clean(_first(merged, "typeBDR", "typeBdr")),
        _clean(_first(merged, "activity", "mainActivity")),
        _clean(
            _first(
                merged,
                "industryClassification",
                "industry",
                "classification",
            )
        ),
        _clean(_first(merged, "website", "site")),
        _parse_date(
            _first(
                merged,
                "listingDate",
                "quotedSince",
                "dateListing",
            )
        ),
        SOURCE_CODE,
    )


COMPANY_UPSERT_SQL = """
insert into investimento.b3_empresas_listadas (
    codigo_cvm,
    cnpj,
    razao_social,
    nome_pregao,
    codigo_emissor,
    segmento_listagem,
    mercado,
    tipo_bdr,
    atividade,
    classificacao_setorial,
    site,
    data_listagem,
    fonte
)
values (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s
)
on conflict (codigo_cvm) do update set
    cnpj = excluded.cnpj,
    razao_social = excluded.razao_social,
    nome_pregao = excluded.nome_pregao,
    codigo_emissor = excluded.codigo_emissor,
    segmento_listagem = excluded.segmento_listagem,
    mercado = excluded.mercado,
    tipo_bdr = excluded.tipo_bdr,
    atividade = excluded.atividade,
    classificacao_setorial = excluded.classificacao_setorial,
    site = excluded.site,
    data_listagem = excluded.data_listagem,
    fonte = excluded.fonte,
    coletado_em = now()
"""

TICKER_UPSERT_SQL = """
insert into investimento.b3_tickers (
    ticker,
    codigo_cvm,
    isin,
    principal,
    fonte
)
values (%s, %s, %s, %s, %s)
on conflict (ticker) do update set
    codigo_cvm = excluded.codigo_cvm,
    isin = excluded.isin,
    principal = excluded.principal,
    fonte = excluded.fonte,
    coletado_em = now()
"""


def collect_and_upsert(conn) -> tuple[int, int, int]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 projeto-investimento/1.0 "
                "(consulta automatizada de dados publicos B3)"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": (
                "https://sistemaswebb3-listados.b3.com.br/"
                "listedCompaniesPage/"
            ),
        }
    )

    companies = fetch_all_companies(session)
    company_rows: list[tuple] = []
    ticker_rows: list[tuple] = []
    detail_errors = 0

    for index, initial in enumerate(companies, start=1):
        code_cvm = _clean(
            _first(initial, "codeCVM", "codeCvm", "codigoCVM")
        )
        if not code_cvm:
            continue

        try:
            detail = fetch_company_detail(session, code_cvm)
            company_rows.append(normalize_company(initial, detail))

            for ticker, isin, principal in extract_tickers(detail):
                ticker_rows.append(
                    (ticker, code_cvm, isin, principal, SOURCE_CODE)
                )

        except Exception:
            # Mantém o cadastro básico mesmo se o detalhe de uma companhia
            # específica estiver temporariamente indisponível.
            detail_errors += 1
            company_rows.append(normalize_company(initial, {}))

        # Pequena pausa para não sobrecarregar o portal público da B3.
        if index % 20 == 0:
            time.sleep(0.35)

    with conn.cursor() as cur:
        if company_rows:
            cur.executemany(COMPANY_UPSERT_SQL, company_rows)
        if ticker_rows:
            cur.executemany(TICKER_UPSERT_SQL, ticker_rows)

    return len(company_rows), len(ticker_rows), detail_errors
