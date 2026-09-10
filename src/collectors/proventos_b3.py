from __future__ import annotations

import base64
import json
import re
import time
import unicodedata
from bisect import bisect_right
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import requests


PROCESS_NAME = "proventos_b3"
SOURCE_CODE = "B3_PROVENTOS"
SOURCE_NAME = "B3 - Proventos em dinheiro"

API_BASE = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "listedCompaniesProxy/CompanyCall"
)

URL_EMPRESAS = f"{API_BASE}/GetInitialCompanies"
URL_PROVENTOS = f"{API_BASE}/GetListedCashDividends"

DATA_INICIAL = date(2019, 1, 1)

PAGE_SIZE = 120
MAX_PAGES = 100
RETRIES = 5
TIMEOUT = (20, 90)
REQUEST_DELAY = 0.12
INSERT_BATCH_SIZE = 2000


class B3ApiError(RuntimeError):
    pass


def normalizar_texto(valor) -> str:
    texto = str(valor or "").strip()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(
            caractere
        )
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto,
    )

    return texto.upper().strip()


def normalizar_codigo_cvm(valor) -> str:
    texto = re.sub(
        r"\D",
        "",
        str(valor or ""),
    )

    texto = texto.lstrip("0")

    return texto or "0"


def parse_data(valor) -> date | None:
    texto = str(
        valor or ""
    ).strip()

    if not texto:
        return None

    formatos = (
        ("%d/%m/%Y", 10),
        ("%Y-%m-%d", 10),
        ("%Y-%m-%dT%H:%M:%S", 19),
    )

    for formato, tamanho in formatos:
        try:
            return datetime.strptime(
                texto[:tamanho],
                formato,
            ).date()

        except ValueError:
            pass

    return None


def parse_decimal(
    valor,
) -> Decimal | None:
    texto = (
        str(valor or "")
        .strip()
        .replace(
            "R$",
            "",
        )
        .replace(
            " ",
            "",
        )
    )

    if not texto:
        return None

    if (
        ","
        in texto
        and "."
        in texto
    ):
        if (
            texto.rfind(",")
            > texto.rfind(".")
        ):
            texto = texto.replace(
                ".",
                "",
            )

            texto = texto.replace(
                ",",
                ".",
            )

        else:
            texto = texto.replace(
                ",",
                "",
            )

    elif "," in texto:
        texto = texto.replace(
            ",",
            ".",
        )

    try:
        return Decimal(
            texto
        )

    except InvalidOperation:
        return None


def codificar_payload(
    payload: dict,
) -> str:
    bruto = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(
            ",",
            ":",
        ),
    ).encode(
        "utf-8"
    )

    token = base64.b64encode(
        bruto
    ).decode(
        "ascii"
    )

    return quote(
        token,
        safe="",
    )


def criar_session() -> requests.Session:
    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; "
                "projeto-investimento/1.0; "
                "dados-publicos)"
            ),
            "Accept": (
                "application/json, "
                "text/plain, "
                "*/*"
            ),
            "Accept-Language": (
                "pt-BR,pt;q=0.9,"
                "en;q=0.8"
            ),
            "Referer": (
                "https://www.b3.com.br/"
            ),
        }
    )

    return session


def requisitar_json(
    session: requests.Session,
    endpoint: str,
    payload: dict,
) -> dict:
    url = (
        f"{endpoint}/"
        f"{codificar_payload(payload)}"
    )

    ultimo_erro = None

    for tentativa in range(
        1,
        RETRIES + 1,
    ):
        try:
            resposta = session.get(
                url,
                timeout=TIMEOUT,
            )

            resposta.raise_for_status()

            dados = resposta.json()

            if not isinstance(
                dados,
                dict,
            ):
                raise B3ApiError(
                    "Resposta B3 inválida."
                )

            time.sleep(
                REQUEST_DELAY
            )

            return dados

        except Exception as exc:
            ultimo_erro = exc

            if tentativa < RETRIES:
                time.sleep(
                    tentativa * 2
                )

    raise B3ApiError(
        (
            "Falha ao consultar a B3 "
            f"após {RETRIES} tentativas: "
            f"{ultimo_erro}"
        )
    )


def garantir_fonte(
    conn,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.fontes_dados (
                codigo,
                nome,
                tipo,
                oficial,
                url_base,
                periodicidade,
                finalidade,
                ativa
            )
            values (
                %s,
                %s,
                'PROVENTOS',
                true,
                %s,
                'DIARIA',
                %s,
                true
            )
            on conflict (codigo)
            do update set
                nome =
                    excluded.nome,

                tipo =
                    excluded.tipo,

                oficial =
                    true,

                url_base =
                    excluded.url_base,

                periodicidade =
                    excluded.periodicidade,

                finalidade =
                    excluded.finalidade,

                ativa =
                    true,

                atualizado_em =
                    now()
            """,
            (
                SOURCE_CODE,
                SOURCE_NAME,
                URL_PROVENTOS,
                (
                    "Dividendos, JCP e "
                    "rendimentos em dinheiro "
                    "divulgados pela B3 para "
                    "ações brasileiras."
                ),
            ),
        )


def carregar_universo(
    conn,
    ticker: str | None = None,
) -> dict[str, list[dict]]:
    codigo_alvo = None

    if ticker:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    codigo_cvm
                from
                    investimento.vw_acoes_validacao_oficial_atual
                where
                    elegivel_analise = true
                    and upper(ticker)
                        = upper(%s)
                    and codigo_cvm
                        is not null
                limit 1
                """,
                (
                    ticker.strip(),
                ),
            )

            row = cur.fetchone()

        if not row:
            raise ValueError(
                (
                    f"Ticker {ticker!r} "
                    "não encontrado."
                )
            )

        codigo_alvo = (
            normalizar_codigo_cvm(
                row[0]
            )
        )

    with conn.cursor() as cur:
        if codigo_alvo:
            cur.execute(
                """
                select
                    ativo_id,
                    ticker,
                    subclasse,
                    codigo_cvm
                from
                    investimento.vw_acoes_validacao_oficial_atual
                where
                    elegivel_analise = true
                    and codigo_cvm
                        is not null
                    and ltrim(
                        codigo_cvm,
                        '0'
                    ) = %s
                order by
                    ticker
                """,
                (
                    codigo_alvo,
                ),
            )

        else:
            cur.execute(
                """
                select
                    ativo_id,
                    ticker,
                    subclasse,
                    codigo_cvm
                from
                    investimento.vw_acoes_validacao_oficial_atual
                where
                    elegivel_analise = true
                    and codigo_cvm
                        is not null
                order by
                    codigo_cvm,
                    ticker
                """
            )

        rows = cur.fetchall()

    universo: dict[
        str,
        list[dict],
    ] = {}

    for (
        ativo_id,
        ticker_db,
        subclasse,
        codigo_cvm,
    ) in rows:

        codigo = (
            normalizar_codigo_cvm(
                codigo_cvm
            )
        )

        universo.setdefault(
            codigo,
            [],
        ).append(
            {
                "ativo_id":
                    int(
                        ativo_id
                    ),

                "ticker":
                    str(
                        ticker_db
                    ).upper(),

                "subclasse":
                    normalizar_texto(
                        subclasse
                    ),
            }
        )

    if not universo:
        raise RuntimeError(
            "Universo de ações vazio."
        )

    return universo


def carregar_calendario_pregoes(
    conn,
) -> list[date]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct
                data
            from
                investimento.cotacoes_diarias
            where
                data >= %s
                and fonte = 'B3_HIST'
            order by
                data
            """,
            (
                DATA_INICIAL,
            ),
        )

        calendario = [
            row[0]
            for row in cur.fetchall()
        ]

    if not calendario:
        raise RuntimeError(
            "Calendário de pregões B3 vazio."
        )

    return calendario


def proximo_pregao(
    data_com: date,
    calendario: list[date],
) -> date | None:
    posicao = bisect_right(
        calendario,
        data_com,
    )

    if posicao >= len(
        calendario
    ):
        return None

    return calendario[
        posicao
    ]


def total_paginas(
    dados: dict,
) -> int | None:
    try:
        return int(
            (
                dados.get(
                    "page"
                )
                or {}
            ).get(
                "totalPages"
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def obter_catalogo_empresas(
    session: requests.Session,
) -> dict[str, dict]:
    catalogo: dict[
        str,
        dict,
    ] = {}

    for pagina in range(
        1,
        MAX_PAGES + 1,
    ):
        dados = requisitar_json(
            session,
            URL_EMPRESAS,
            {
                "language":
                    "pt-br",

                "pageNumber":
                    pagina,

                "pageSize":
                    PAGE_SIZE,
            },
        )

        resultados = (
            dados.get(
                "results"
            )
            or []
        )

        if not isinstance(
            resultados,
            list,
        ):
            raise B3ApiError(
                (
                    "Catálogo B3 com "
                    "campo results inválido."
                )
            )

        for item in resultados:
            if not isinstance(
                item,
                dict,
            ):
                continue

            codigo = (
                normalizar_codigo_cvm(
                    item.get(
                        "codeCVM"
                    )
                )
            )

            trading_name = str(
                item.get(
                    "tradingName"
                )
                or ""
            ).strip()

            if (
                codigo != "0"
                and trading_name
            ):
                catalogo[
                    codigo
                ] = {
                    "trading_name":
                        trading_name,

                    "issuing_company":
                        str(
                            item.get(
                                "issuingCompany"
                            )
                            or ""
                        ).strip(),
                }

        paginas = total_paginas(
            dados
        )

        if not resultados:
            break

        if (
            paginas is not None
            and pagina >= paginas
        ):
            break

        if (
            paginas is None
            and len(
                resultados
            ) < PAGE_SIZE
        ):
            break

    if not catalogo:
        raise B3ApiError(
            (
                "Catálogo de empresas "
                "B3 retornou vazio."
            )
        )

    return catalogo


def limpar_trading_name(
    valor: str,
) -> str:
    texto = normalizar_texto(
        valor
    )

    texto = re.sub(
        r"[^A-Z0-9 ]+",
        "",
        texto,
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto,
    )

    return texto.strip()


def obter_proventos_empresa(
    session: requests.Session,
    trading_name: str,
) -> list[dict]:
    nome = limpar_trading_name(
        trading_name
    )

    if not nome:
        return []

    acumulado: list[
        dict
    ] = []

    for pagina in range(
        1,
        MAX_PAGES + 1,
    ):
        dados = requisitar_json(
            session,
            URL_PROVENTOS,
            {
                "language":
                    "pt-br",

                "pageNumber":
                    pagina,

                "pageSize":
                    PAGE_SIZE,

                "tradingName":
                    nome,
            },
        )

        resultados = (
            dados.get(
                "results"
            )
            or []
        )

        if not isinstance(
            resultados,
            list,
        ):
            raise B3ApiError(
                (
                    "Proventos B3 com "
                    "campo results inválido."
                )
            )

        acumulado.extend(
            item
            for item in resultados
            if isinstance(
                item,
                dict,
            )
        )

        paginas = total_paginas(
            dados
        )

        if not resultados:
            break

        if (
            paginas is not None
            and pagina >= paginas
        ):
            break

        if (
            paginas is None
            and len(
                resultados
            ) < PAGE_SIZE
        ):
            break

    return acumulado


def normalizar_tipo(
    valor,
) -> str | None:
    texto = normalizar_texto(
        valor
    )

    if (
        "JRS CAP PROPRIO"
        in texto

        or
        "JUROS SOBRE CAPITAL"
        in texto

        or
        texto == "JCP"
    ):
        return "JCP"

    if "DIVIDENDO" in texto:
        return "DIVIDENDO"

    if "RENDIMENTO" in texto:
        return "RENDIMENTO"

    return None


def normalizar_classe(
    valor,
) -> str | None:
    texto = (
        normalizar_texto(
            valor
        )
        .replace(
            " ",
            "",
        )
    )

    for classe in (
        "PNA",
        "PNB",
        "PNC",
    ):
        if texto.startswith(
            classe
        ):
            return classe

    if texto.startswith(
        "PN"
    ):
        return "PN"

    if texto.startswith(
        "ON"
    ):
        return "ON"

    if (
        texto.startswith(
            "UNT"
        )
        or
        texto.startswith(
            "UNIT"
        )
    ):
        return "UNIT"

    return None


def ticker_padrao(
    ticker: str,
    classe: str,
) -> bool:
    padroes = {
        "ON":
            r"3B?$",

        "PN":
            r"4B?$",

        "PNA":
            r"5B?$",

        "PNB":
            r"6B?$",

        "PNC":
            r"7B?$",

        "UNIT":
            r"11B?$",
    }

    padrao = padroes.get(
        classe
    )

    return bool(
        padrao
        and re.search(
            padrao,
            ticker.upper(),
        )
    )


def ativos_para_classe(
    ativos: list[dict],
    classe: str,
) -> list[dict]:
    exatos = [
        ativo
        for ativo in ativos
        if ativo.get(
            "subclasse"
        ) == classe
    ]

    padrao = [
        ativo
        for ativo in exatos
        if ticker_padrao(
            ativo[
                "ticker"
            ],
            classe,
        )
    ]

    if padrao:
        return padrao

    if len(
        exatos
    ) == 1:
        return exatos

    return []


def normalizar_empresa(
    codigo_cvm: str,
    ativos: list[dict],
    itens: list[dict],
    calendario: list[date],
) -> tuple[
    list[dict],
    dict,
]:
    registros: list[
        dict
    ] = []

    stats = {
        "lidos":
            0,

        "ignorados_tipo":
            0,

        "ignorados_data":
            0,

        "ignorados_valor":
            0,

        "ignorados_classe":
            0,

        "sem_data_ex":
            0,
    }

    for item in itens:
        stats[
            "lidos"
        ] += 1

        tipo = normalizar_tipo(
            item.get(
                "corporateAction"
            )
        )

        if tipo is None:
            stats[
                "ignorados_tipo"
            ] += 1

            continue

        data_com = parse_data(
            item.get(
                "lastDatePriorEx"
            )
            or
            item.get(
                "lastDateTimePriorEx"
            )
        )

        if (
            data_com is None
            or data_com
            < DATA_INICIAL
        ):
            stats[
                "ignorados_data"
            ] += 1

            continue

        valor = parse_decimal(
            item.get(
                "valueCash"
            )
        )

        if (
            valor is None
            or valor <= 0
        ):
            stats[
                "ignorados_valor"
            ] += 1

            continue

        classe = (
            normalizar_classe(
                item.get(
                    "typeStock"
                )
            )
        )

        if classe is None:
            stats[
                "ignorados_classe"
            ] += 1

            continue

        destino = (
            ativos_para_classe(
                ativos,
                classe,
            )
        )

        if not destino:
            stats[
                "ignorados_classe"
            ] += 1

            continue

        data_pagamento = (
            parse_data(
                item.get(
                    "paymentDate"
                )
                or
                item.get(
                    "datePayment"
                )
                or
                item.get(
                    "paymentDateTime"
                )
            )
        )

        data_ex = (
            proximo_pregao(
                data_com,
                calendario,
            )
        )

        if data_ex is None:
            stats[
                "sem_data_ex"
            ] += 1

        for ativo in destino:
            registros.append(
                {
                    "ativo_id":
                        ativo[
                            "ativo_id"
                        ],

                    "ticker":
                        ativo[
                            "ticker"
                        ],

                    "codigo_cvm":
                        codigo_cvm,

                    "tipo":
                        tipo,

                    "data_com":
                        data_com,

                    "data_ex":
                        data_ex,

                    "data_pagamento":
                        data_pagamento,

                    "valor_por_unidade":
                        valor,
                }
            )

    return (
        registros,
        stats,
    )


def deduplicar(
    registros: list[dict],
) -> list[dict]:
    unicos: dict[
        tuple,
        dict,
    ] = {}

    for registro in registros:
        chave = (
            registro[
                "ativo_id"
            ],

            registro[
                "tipo"
            ],

            registro[
                "data_com"
            ],

            registro[
                "data_ex"
            ],

            registro[
                "data_pagamento"
            ],

            registro[
                "valor_por_unidade"
            ],
        )

        unicos[
            chave
        ] = registro

    return list(
        unicos.values()
    )


def dividir_em_lotes(
    registros: list[dict],
    tamanho: int,
):
    for inicio in range(
        0,
        len(
            registros
        ),
        tamanho,
    ):
        yield registros[
            inicio:
            inicio + tamanho
        ]


def gravar(
    conn,
    registros: list[dict],
    universo: dict[
        str,
        list[dict],
    ],
    modo_ticker: bool,
) -> int:
    ativos_alvo = sorted(
        {
            ativo[
                "ativo_id"
            ]
            for ativos
            in universo.values()
            for ativo
            in ativos
        }
    )

    with conn.cursor() as cur:
        if modo_ticker:
            cur.execute(
                """
                delete from
                    investimento.proventos
                where
                    fonte = %s
                    and ativo_id
                        = any(%s)
                """,
                (
                    SOURCE_CODE,
                    ativos_alvo,
                ),
            )

        else:
            cur.execute(
                """
                delete from
                    investimento.proventos
                where
                    fonte = %s
                """,
                (
                    SOURCE_CODE,
                ),
            )

        for lote in dividir_em_lotes(
            registros,
            INSERT_BATCH_SIZE,
        ):
            cur.executemany(
                """
                insert into
                    investimento.proventos (
                        ativo_id,
                        tipo,
                        data_com,
                        data_ex,
                        data_pagamento,
                        valor_por_unidade,
                        status,
                        fonte,
                        fonte_oficial,
                        coletado_em
                    )
                values (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'CONFIRMADO',
                    %s,
                    true,
                    now()
                )
                """,
                [
                    (
                        registro[
                            "ativo_id"
                        ],

                        registro[
                            "tipo"
                        ],

                        registro[
                            "data_com"
                        ],

                        registro[
                            "data_ex"
                        ],

                        registro[
                            "data_pagamento"
                        ],

                        registro[
                            "valor_por_unidade"
                        ],

                        SOURCE_CODE,
                    )
                    for registro
                    in lote
                ],
            )

    return len(
        registros
    )


def coletar(
    conn,
    ticker: str | None = None,
) -> dict:
    universo = carregar_universo(
        conn,
        ticker=ticker,
    )

    calendario = (
        carregar_calendario_pregoes(
            conn
        )
    )

    session = criar_session()

    try:
        catalogo = (
            obter_catalogo_empresas(
                session
            )
        )

        codigos = sorted(
            universo
        )

        sem_catalogo = [
            codigo
            for codigo
            in codigos
            if codigo
            not in catalogo
        ]

        if (
            ticker
            and sem_catalogo
        ):
            raise RuntimeError(
                (
                    "Empresa do ticker "
                    "não localizada no "
                    "catálogo público da B3."
                )
            )

        if not ticker:
            cobertura = (
                (
                    len(
                        codigos
                    )
                    - len(
                        sem_catalogo
                    )
                )
                / len(
                    codigos
                )
            )

            if cobertura < 0.90:
                raise RuntimeError(
                    (
                        "Cobertura do catálogo "
                        "B3 abaixo de 90%; "
                        "a carga foi cancelada "
                        "e os dados anteriores "
                        "foram preservados."
                    )
                )

        totais = {
            "empresas_universo":
                len(
                    codigos
                ),

            "empresas_resolvidas":
                0,

            "empresas_sem_catalogo":
                len(
                    sem_catalogo
                ),

            "lidos":
                0,

            "ignorados_tipo":
                0,

            "ignorados_data":
                0,

            "ignorados_valor":
                0,

            "ignorados_classe":
                0,

            "sem_data_ex":
                0,
        }

        todos: list[
            dict
        ] = []

        for (
            indice,
            codigo,
        ) in enumerate(
            codigos,
            start=1,
        ):
            empresa = (
                catalogo.get(
                    codigo
                )
            )

            if not empresa:
                continue

            totais[
                "empresas_resolvidas"
            ] += 1

            print(
                (
                    f"[{indice}/"
                    f"{len(codigos)}] "
                    f"CVM {codigo} - "
                    f"{empresa['trading_name']}"
                )
            )

            itens = (
                obter_proventos_empresa(
                    session,
                    empresa[
                        "trading_name"
                    ],
                )
            )

            (
                registros,
                stats,
            ) = normalizar_empresa(
                codigo,
                universo[
                    codigo
                ],
                itens,
                calendario,
            )

            todos.extend(
                registros
            )

            for chave in (
                "lidos",
                "ignorados_tipo",
                "ignorados_data",
                "ignorados_valor",
                "ignorados_classe",
                "sem_data_ex",
            ):
                totais[
                    chave
                ] += stats[
                    chave
                ]

        finais = deduplicar(
            todos
        )

        totais[
            "gravados"
        ] = gravar(
            conn,
            finais,
            universo,
            modo_ticker=bool(
                ticker
            ),
        )

        totais[
            "duplicados_removidos"
        ] = (
            len(
                todos
            )
            - len(
                finais
            )
        )

        return totais

    finally:
        session.close()
