from __future__ import annotations

import math

from src.jobs import coletar_b3_programas_bdr as base


MAX_UNAVAILABLE_RATIO = 0.05
MIN_ALLOWED_UNAVAILABLE = 5

_original_persist = base.persist


def collect_program_type(
    session,
    tipo_programa: str,
    base_url: str,
):
    """
    Coleta todas as páginas disponíveis.

    O total informado pela B3 pode incluir programas que
    não são entregues na tabela ou não possuem ticker válido.
    A diferença aceita é limitada a 5%.
    """
    first_url, first_html = base.request_page(
        session,
        base_url,
        1,
    )

    official_total = base.parse_total(first_html)
    pages = math.ceil(
        official_total / base.PAGE_SIZE
    )

    rows = base.parse_page(
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

        rows.extend(
            base.parse_page(
                tipo_programa,
                page_url,
                html,
                page,
            )
        )

    tickers = [
        row.ticker
        for row in rows
    ]

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

    valid_total = len(rows)

    if valid_total > official_total:
        raise RuntimeError(
            f"volume extraído maior que o total oficial "
            f"{tipo_programa}: "
            f"válidos={valid_total} "
            f"oficial={official_total}"
        )

    unavailable = (
        official_total - valid_total
    )

    maximum_unavailable = max(
        MIN_ALLOWED_UNAVAILABLE,
        math.ceil(
            official_total
            * MAX_UNAVAILABLE_RATIO
        ),
    )

    if unavailable > maximum_unavailable:
        raise RuntimeError(
            f"diferença excessiva em "
            f"{tipo_programa}: "
            f"válidos={valid_total} "
            f"oficial={official_total} "
            f"não_disponíveis={unavailable} "
            f"máximo={maximum_unavailable}"
        )

    if valid_total < base.MIN_ROWS[tipo_programa]:
        raise RuntimeError(
            f"volume abaixo do mínimo "
            f"{tipo_programa}: "
            f"{valid_total} < "
            f"{base.MIN_ROWS[tipo_programa]}"
        )

    coverage = (
        valid_total / official_total
        if official_total
        else 0.0
    )

    base.progress(
        f"{tipo_programa}: "
        f"páginas={pages} "
        f"total_oficial={official_total} "
        f"programas_validos={valid_total} "
        f"não_disponíveis={unavailable} "
        f"cobertura={coverage:.2%}"
    )

    # O código principal deve comparar e registrar
    # somente o volume realmente persistido.
    return rows, valid_total


def persist(
    conn,
    rows,
    ref_date,
):
    """
    Mantém somente o snapshot mais recente no Supabase.

    A exclusão e a nova gravação ficam na mesma transação.
    Em caso de falha, o snapshot anterior é preservado.
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
