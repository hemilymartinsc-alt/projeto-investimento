from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from zipfile import ZipFile

import requests
from psycopg.rows import dict_row

SOURCE_CODE = "B3_HIST"
PROCESS_NAME = "cotacoes_b3"

BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    ),
    "Accept": "*/*",
}

TIMEOUT = 60


@dataclass(slots=True)
class Cotacao:
    ativo_id: int
    ticker: str
    data: date
    abertura: float
    maxima: float
    minima: float
    fechamento: float
    volume: float
    isin: str | None


def _preco(campo: str) -> float:
    campo = campo.strip()
    if not campo:
        return 0.0
    return int(campo) / 100


def parse_linha(
    linha: str,
    ativos_por_ticker: dict[str, int],
) -> Cotacao | None:
    if len(linha) < 245:
        return None

    # Registro de cotação.
    if linha[0:2] != "01":
        return None

    # Mercado à vista.
    tipo_mercado = linha[24:27]
    if tipo_mercado != "010":
        return None

    ticker = linha[12:24].strip().upper()

    ativo_id = ativos_por_ticker.get(ticker)
    if ativo_id is None:
        return None

    data_pregao = datetime.strptime(
        linha[2:10], "%Y%m%d"
    ).date()

    abertura = _preco(linha[56:69])
    maxima = _preco(linha[69:82])
    minima = _preco(linha[82:95])
    fechamento = _preco(linha[108:121])
    volume = _preco(linha[170:188])
    isin = linha[230:242].strip() or None

    if fechamento <= 0:
        return None

    if maxima < minima:
        return None

    if abertura < 0 or minima < 0 or volume < 0:
        return None

    return Cotacao(
        ativo_id=ativo_id,
        ticker=ticker,
        data=data_pregao,
        abertura=abertura,
        maxima=maxima,
        minima=minima,
        fechamento=fechamento,
        volume=volume,
        isin=isin,
    )


def carregar_universo(conn) -> dict[str, int]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select
                id,
                ticker
            from investimento.ativos
            where elegivel_analise is true
              and classe in ('ACAO', 'FII')
              and ticker is not null
            """
        )
        rows = cur.fetchall()

    return {
        row["ticker"].strip().upper(): row["id"]
        for row in rows
    }


def _baixar(url: str) -> bytes:
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    conteudo = response.content

    if len(conteudo) < 100:
        raise RuntimeError(
            f"Arquivo B3 muito pequeno/inválido: {url}"
        )

    return conteudo


def baixar_anual(ano: int) -> bytes:
    nome = f"COTAHIST_A{ano}.ZIP"
    return _baixar(f"{BASE_URL}/{nome}")


def baixar_diario(data_ref: date) -> bytes:
    nome = (
        f"COTAHIST_D"
        f"{data_ref.day:02d}"
        f"{data_ref.month:02d}"
        f"{data_ref.year}.ZIP"
    )
    return _baixar(f"{BASE_URL}/{nome}")


def extrair_txt(conteudo_zip: bytes) -> list[str]:
    with ZipFile(BytesIO(conteudo_zip)) as arquivo:
        nomes = [
            nome
            for nome in arquivo.namelist()
            if nome.lower().endswith(".txt")
        ]

        if not nomes:
            raise RuntimeError(
                "ZIP da B3 não contém arquivo TXT."
            )

        with arquivo.open(nomes[0]) as bruto:
            conteudo = bruto.read()

    texto = conteudo.decode(
        "latin-1",
        errors="replace",
    )

    return texto.splitlines()


def parse_arquivo(
    linhas: list[str],
    universo: dict[str, int],
) -> tuple[list[Cotacao], int]:
    registros: list[Cotacao] = []
    lidos = 0

    for linha in linhas:
        if not linha.startswith("01"):
            continue

        lidos += 1

        cotacao = parse_linha(
            linha,
            universo,
        )

        if cotacao is not None:
            registros.append(cotacao)

    return registros, lidos


def gravar_cotacoes(
    conn,
    registros: list[Cotacao],
) -> int:
    if not registros:
        return 0

    dados = [
        (
            item.ativo_id,
            item.data,
            item.abertura,
            item.maxima,
            item.minima,
            item.fechamento,
            None,
            item.volume,
            SOURCE_CODE,
            True,
        )
        for item in registros
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into investimento.cotacoes_diarias (
                ativo_id,
                data,
                abertura,
                maxima,
                minima,
                fechamento,
                fechamento_ajustado,
                volume,
                fonte,
                fonte_oficial,
                coletado_em
            )
            values (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                now()
            )
            on conflict (ativo_id, data, fonte)
            do update set
                abertura = excluded.abertura,
                maxima = excluded.maxima,
                minima = excluded.minima,
                fechamento = excluded.fechamento,
                volume = excluded.volume,
                fonte_oficial = excluded.fonte_oficial,
                coletado_em = now()
            """,
            dados,
        )

    return len(dados)


def coletar_anual(
    conn,
    ano: int,
) -> tuple[int, int]:
    universo = carregar_universo(conn)

    if not universo:
        raise RuntimeError(
            "Universo de Ações/FIIs está vazio."
        )

    zip_bytes = baixar_anual(ano)
    linhas = extrair_txt(zip_bytes)

    registros, lidos = parse_arquivo(
        linhas,
        universo,
    )

    gravados = gravar_cotacoes(
        conn,
        registros,
    )

    return lidos, gravados


def coletar_diario(
    conn,
    data_ref: date,
) -> tuple[int, int]:
    universo = carregar_universo(conn)

    if not universo:
        raise RuntimeError(
            "Universo de Ações/FIIs está vazio."
        )

    zip_bytes = baixar_diario(data_ref)
    linhas = extrair_txt(zip_bytes)

    registros, lidos = parse_arquivo(
        linhas,
        universo,
    )

    gravados = gravar_cotacoes(
        conn,
        registros,
    )

    return lidos, gravados
