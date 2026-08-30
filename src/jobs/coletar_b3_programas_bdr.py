from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

from src.database import connect


SOURCE_CODE = "B3_BDR_PROGRAMS"
PROCESS_NAME = "coletar_programas_bdr_banco_b3"
TZ_BR = ZoneInfo("America/Sao_Paulo")
TIMEOUT = (5, 30)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1
PAGE_SIZE = 60

PROGRAMS = {
    "NAO_PATROCINADO": "https://finservices.b3.com.br/bdr-nao-patrocinado/programas",
    "ETF": "https://finservices.b3.com.br/bdr-de-etf/programa",
}

MIN_ROWS = {
    "NAO_PATROCINADO": 700,
    "ETF": 250,
}
MAX_VARIATION_RATIO = 0.15

TICKER_RE = re.compile(r"^[A-Z0-9]{4,8}\d{2}\*?$")
TOTAL_RE = re.compile(
    r"Exibindo\s+\d+\s*-\s*\d+\s+de\s+([\d.]+)\s+resultados",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProgramRow:
    tipo_programa: str
    ticker: str
    nome_programa: str
    restrito_qualificados: bool
    url_site_emissor: str | None
    url_documento_b3: str | None
    url_pagina_fonte: str
    pagina: int


class TableParser(HTMLParser):
    """Extrai linhas da tabela sem depender de lxml/BeautifulSoup."""

    def __init__(self, page_url: str):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.in_tr = False
        self.in_td = False
        self.cell_text: list[str] = []
        self.cell_links: list[str] = []
        self.row_cells: list[tuple[str, list[str]]] = []
        self.rows: list[tuple[str, str, list[str], list[str]]] = []
        self.visible_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.in_tr = True
            self.row_cells = []
        elif tag == "td" and self.in_tr:
            self.in_td = True
            self.cell_text = []
            self.cell_links = []
        elif tag == "a" and self.in_td:
            href = attrs.get("href")
            if href:
                self.cell_links.append(urljoin(self.page_url, href))

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        self.visible_text.append(text)
        if self.in_td:
            self.cell_text.append(text)

    def handle_endtag(self, tag):
        if tag == "td" and self.in_td:
            self.row_cells.append(
                (" ".join(self.cell_text).strip(), list(self.cell_links))
            )
            self.in_td = False
        elif tag == "tr" and self.in_tr:
            self._finish_row()
            self.in_tr = False
            self.row_cells = []

    def _finish_row(self):
        if len(self.row_cells) < 2:
            return
        company = self.row_cells[0][0].strip()
        ticker_raw = self.row_cells[1][0].strip().upper().replace(" ", "")
        if not company or not TICKER_RE.fullmatch(ticker_raw):
            return

        links = []
        for _, cell_links in self.row_cells:
            links.extend(cell_links)

        site_links = [
            link
            for link in links
            if "finservices.b3.com.br" not in link
            and "b3.com.br" not in link
            and not link.lower().endswith((".pdf", ".zip"))
        ]
        b3_links = [
            link
            for link in links
            if "b3.com.br" in link or "finservices.b3.com.br" in link
        ]
        self.rows.append(
            (company, ticker_raw, site_links, b3_links)
        )


def progress(message: str) -> None:
    print(f"B3 programas BDR: {message}", flush=True)


def request_page(session: requests.Session, base_url: str, page: int) -> tuple[str, str]:
    params = {"delta": PAGE_SIZE}
    if page > 1:
        params["start"] = page

    errors = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(
                base_url,
                params=params,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            if "Programas disponíveis" not in response.text:
                raise RuntimeError("marcador da página oficial não encontrado")
            return response.url, response.text
        except Exception as exc:
            errors.append(str(exc))
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * attempt)

    raise RuntimeError(
        f"falha ao baixar {base_url} página={page}: {errors[-1]}"
    )


def parse_total(html: str) -> int:
    match = TOTAL_RE.search(" ".join(html.split()))
    if not match:
        raise RuntimeError("total oficial 'Exibindo ... de N resultados' não encontrado")
    return int(match.group(1).replace(".", ""))


def parse_page(
    tipo_programa: str,
    page_url: str,
    html: str,
    page: int,
) -> list[ProgramRow]:
    parser = TableParser(page_url)
    parser.feed(html)

    result = []
    for company, ticker_raw, site_links, b3_links in parser.rows:
        restricted = ticker_raw.endswith("*")
        ticker = ticker_raw.rstrip("*")

        # A página tem uma ação de site e uma ação de arquivo por programa.
        # Não usamos nome/ticker para inferir a natureza do programa:
        # o próprio endpoint define NAO_PATROCINADO ou ETF.
        site_url = site_links[0] if site_links else None
        document_url = b3_links[-1] if b3_links else None

        result.append(
            ProgramRow(
                tipo_programa=tipo_programa,
                ticker=ticker,
                nome_programa=company,
                restrito_qualificados=restricted,
                url_site_emissor=site_url,
                url_documento_b3=document_url,
                url_pagina_fonte=page_url,
                pagina=page,
            )
        )
    return result


def collect_program_type(
    session: requests.Session,
    tipo_programa: str,
    base_url: str,
) -> tuple[list[ProgramRow], int]:
    first_url, first_html = request_page(session, base_url, 1)
    official_total = parse_total(first_html)
    pages = math.ceil(official_total / PAGE_SIZE)

    rows = parse_page(tipo_programa, first_url, first_html, 1)
    for page in range(2, pages + 1):
        page_url, html = request_page(session, base_url, page)
        page_total = parse_total(html)
        if page_total != official_total:
            raise RuntimeError(
                f"total oficial mudou durante coleta {tipo_programa}: "
                f"{official_total} -> {page_total}"
            )
        rows.extend(parse_page(tipo_programa, page_url, html, page))

    tickers = [row.ticker for row in rows]
    duplicates = sorted(
        ticker for ticker in set(tickers) if tickers.count(ticker) > 1
    )
    if duplicates:
        raise RuntimeError(
            f"tickers duplicados em {tipo_programa}: {duplicates[:10]}"
        )
    if len(rows) != official_total:
        raise RuntimeError(
            f"contagem incompleta {tipo_programa}: "
            f"extraídos={len(rows)} oficial={official_total}"
        )
    if len(rows) < MIN_ROWS[tipo_programa]:
        raise RuntimeError(
            f"volume abaixo do mínimo {tipo_programa}: "
            f"{len(rows)} < {MIN_ROWS[tipo_programa]}"
        )

    progress(
        f"{tipo_programa}: páginas={pages} linhas={len(rows)} "
        f"total_oficial={official_total}"
    )
    return rows, official_total


def latest_counts(conn) -> dict[str, int] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            with ultima as (
                select max(data_referencia) data_referencia
                from investimento.b3_bdr_programas_snapshot
            )
            select tipo_programa, count(*)
            from investimento.b3_bdr_programas_snapshot s
            join ultima u using (data_referencia)
            group by tipo_programa
            """
        )
        rows = cur.fetchall()

    if not rows:
        return None
    return {tipo: int(qtd) for tipo, qtd in rows}


def validate_variation(
    current: dict[str, int],
    previous: dict[str, int] | None,
) -> None:
    if not previous:
        return
    errors = []
    for tipo, current_value in current.items():
        previous_value = previous.get(tipo)
        if not previous_value:
            continue
        ratio = abs(current_value - previous_value) / previous_value
        if ratio > MAX_VARIATION_RATIO:
            errors.append(
                f"{tipo}: anterior={previous_value} atual={current_value} "
                f"variação={ratio:.2%} limite={MAX_VARIATION_RATIO:.2%}"
            )
    if errors:
        raise RuntimeError("sanidade de variação falhou: " + "; ".join(errors))


def start_log(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo, processo, iniciado_em, status,
                registros_lidos, registros_gravados, mensagem
            )
            values (%s, %s, now(), 'INICIADO', 0, 0, %s)
            returning id
            """,
            (
                SOURCE_CODE,
                PROCESS_NAME,
                "Coleta oficial dos programas correntes de BDR do Banco B3.",
            ),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def finish_log(
    conn,
    log_id: int,
    status: str,
    read: int,
    written: int,
    message: str,
) -> None:
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
            (status, read, written, message, log_id),
        )
    conn.commit()


def persist(conn, rows: list[ProgramRow], ref_date) -> int:
    # Somente depois da coleta completa e de toda a sanidade.
    with conn.cursor() as cur:
        cur.execute(
            """
            delete from investimento.b3_bdr_programas_snapshot
            where data_referencia = %s
            """,
            (ref_date,),
        )
        for row in rows:
            cur.execute(
                """
                insert into investimento.b3_bdr_programas_snapshot (
                    data_referencia,
                    tipo_programa,
                    ticker,
                    nome_programa,
                    restrito_qualificados,
                    url_site_emissor,
                    url_documento_b3,
                    url_pagina_fonte,
                    pagina,
                    dados_raw
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    ref_date,
                    row.tipo_programa,
                    row.ticker,
                    row.nome_programa,
                    row.restrito_qualificados,
                    row.url_site_emissor,
                    row.url_documento_b3,
                    row.url_pagina_fonte,
                    row.pagina,
                    json.dumps(
                        {
                            "tipo_programa": row.tipo_programa,
                            "ticker": row.ticker,
                            "nome_programa": row.nome_programa,
                            "pagina": row.pagina,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        written = len(rows)
    conn.commit()
    return written


def main() -> int:
    ref_date = datetime.now(TZ_BR).date()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/150 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Referer": "https://finservices.b3.com.br/",
        }
    )

    with connect() as conn:
        log_id = start_log(conn)
        total_read = 0
        try:
            previous = latest_counts(conn)
            all_rows: list[ProgramRow] = []
            current_counts = {}

            for tipo, url in PROGRAMS.items():
                rows, official_total = collect_program_type(
                    session, tipo, url
                )
                all_rows.extend(rows)
                current_counts[tipo] = official_total
                total_read += official_total

            cross = {}
            for row in all_rows:
                cross.setdefault(row.ticker, set()).add(row.tipo_programa)
            conflicting = sorted(
                ticker for ticker, kinds in cross.items() if len(kinds) > 1
            )
            if conflicting:
                raise RuntimeError(
                    "ticker presente simultaneamente em tipos de programa: "
                    + ", ".join(conflicting[:10])
                )

            validate_variation(current_counts, previous)
            written = persist(conn, all_rows, ref_date)

            message = (
                f"Snapshot {ref_date}: "
                f"NAO_PATROCINADO={current_counts['NAO_PATROCINADO']}; "
                f"ETF={current_counts['ETF']}; total={written}. "
                "Coleta completa e sanidade aprovada."
            )
            finish_log(
                conn, log_id, "SUCESSO", total_read, written, message
            )
            progress(message)
            return 0
        except Exception as exc:
            conn.rollback()
            finish_log(
                conn,
                log_id,
                "ERRO",
                total_read,
                0,
                f"Coleta rejeitada sem substituir snapshot vigente: {exc}",
            )
            raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        progress(f"ERRO: {exc}")
        raise
