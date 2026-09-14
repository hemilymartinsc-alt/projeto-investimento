from __future__ import annotations

import math

from src.jobs import coletar_b3_programas_bdr as base


MAX_INVALID_TICKER_RATIO = 0.05
MIN_ALLOWED_INVALID_ROWS = 5

_original_persist = base.persist


class AuditedTableParser(base.TableParser):
    """
    Conta todas as linhas exibidas pela tabela oficial,
    inclusive aquelas sem um código BDR válido.
    """

    def __init__(self, page_url: str):
        super().__init__(page_url)
        self.table_row_count = 0
        self.invalid_rows: list[str] = []

    def _finish_row(self):
        if len(self.row_cells) < 2:
            return

        self.table_row_count += 1

        company = self.row_cells[0][0].strip()
        ticker_raw = (
            self.row_cells[1][0]
            .strip()
            .upper()
            .replace(" ", "")
        )

        if (
            not company
            or not base.TICKER_RE.fullmatch(ticker_raw)
        ):
            self.invalid_rows.append(
                f"empresa={company!r} "
                f"codigo={ticker_raw!r}"
            )
            return

        super()._finish_row()


def parse_page(
    tipo_programa: str,
    page_url: str,
    html: str,
    page: int,
):
    parser = AuditedTableParser(page_url)
    parser.feed(html)

    result = []

    for (
        company,
        ticker_raw,
        site_links,
        b3_links,
    ) in parser.rows:
        restricted = ticker_raw.endswith("*")
        ticker = ticker_raw.rstrip("*")

        site_url = (
            site_links[0]
            if site_links
            else None
        )

        document_url = (
            b3_links[-1]
            if b3_links
            else None
        )

        result.append(
            base.ProgramRow(
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

    return (
        result,
        parser.table_row_count,
        parser.invalid_rows,
    )


def collect_program_type(
    session,
    tipo_programa: str,
    base_url: str,
):
    first_url, first_html = base.request_page(
        session,
        base_url,
        1,
    )

    official_total = base.parse_total(first_html)
    pages = math.ceil(
        official_total / base.PAGE_SIZE
    )

    (
        rows,
        observed_rows,
        invalid_rows,
    ) = parse_page(
        tipo_programa,
        first_url,
        first_html,
        1,
    )

    for page in range(2, pages + 1):
        page_url, html = base.request_page(
            session,
            base_url,
            page,
        )

        page_total = base.parse_total(html)

        if page_total != official_total:
            raise RuntimeError(
                "total oficial mudou durante coleta "
                f"{tipo_programa}: "
                f"{official_total} -> {page_total}"
            )

        (
            page_rows,
            page_observed,
            page_invalid,
        ) = parse_page(
            tipo_programa,
            page_url,
            html,
            page,
        )

        rows.extend(page_rows)
        observed_rows += page_observed
        invalid_rows.extend(page_invalid)

    if observed_rows != official_total:
        raise RuntimeError(
            f"tabela oficial incompleta "
            f"{tipo_programa}: "
            f"linhas_observadas={observed_rows} "
            f"total_oficial={official_total}"
        )

    tickers = [row.ticker for row in rows]

    duplicates = sorted(
        ticker
        for ticker in set(tickers)
        if tickers.count(ticker) > 1
    )

    if duplicates:
        raise RuntimeError(
            f"tickers duplicados em "
            f"{tipo_programa}: "
            f"{duplicates[:10]}"
        )

    invalid_count = len(invalid_rows)

    max_invalid = max(
        MIN_ALLOWED_INVALID_ROWS,
        math.ceil(
            official_total
            * MAX_INVALID_TICKER_RATIO
        ),
    )

    if invalid_count > max_invalid:
        raise RuntimeError(
            f"excesso de linhas sem ticker válido "
            f"{tipo_programa}: "
            f"inválidas={invalid_count} "
            f"máximo={max_invalid} "
            f"exemplos={invalid_rows[:5]}"
        )

    if len(rows) < base.MIN_ROWS[tipo_programa]:
        raise RuntimeError(
            f"volume abaixo do mínimo "
            f"{tipo_programa}: "
            f"{len(rows)} < "
            f"{base.MIN_ROWS[tipo_programa]}"
        )

    base.progress(
        f"{tipo_programa}: "
        f"páginas={pages} "
        f"total_oficial={official_total} "
        f"linhas_observadas={observed_rows} "
        f"programas_validos={len(rows)} "
        f"linhas_sem_ticker={invalid_count}"
    )

    # O código principal usa esse segundo valor para
    # comparar e registrar o volume persistido.
    return rows, len(rows)


def persist(
    conn,
    rows,
    ref_date,
):
    """
    Mantém somente o snapshot atual no Supabase.

    Se ocorrer erro durante a gravação, a transação é
    desfeita e o snapshot anterior permanece preservado.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            delete from
                investimento.b3_bdr_programas_snapshot
            where data_referencia <> %s
            """,
            (ref_date,),
        )

    return _original_persist(
        conn,
        rows,
        ref_date,
    )


def main():
    base.collect_program_type = (
        collect_program_type
    )
    base.persist = persist

    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
