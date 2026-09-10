from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import requests


PROCESS_NAME = "composicao_capital_cvm"
SOURCE_LOG_CODE = "CVM_CAPITAL"

URLS = {
    "DFP": (
        "https://dados.cvm.gov.br/dados/"
        "CIA_ABERTA/DOC/DFP/DADOS/"
        "dfp_cia_aberta_{ano}.zip"
    ),
    "ITR": (
        "https://dados.cvm.gov.br/dados/"
        "CIA_ABERTA/DOC/ITR/DADOS/"
        "itr_cia_aberta_{ano}.zip"
    ),
}

FONTES = {
    "DFP": "CVM_DFP_CAPITAL",
    "ITR": "CVM_ITR_CAPITAL",
}

TIMEOUT = (
    30,
    300,
)

RETRIES = 5
INSERT_BATCH_SIZE = 1000


class CVMCapitalError(
    RuntimeError
):
    pass


def normalizar_codigo_cvm(
    valor,
) -> str:
    texto = re.sub(
        r"\D",
        "",
        str(
            valor or ""
        ),
    )

    texto = texto.lstrip(
        "0"
    )

    return texto or "0"


def normalizar_cnpj(
    valor,
) -> str | None:
    texto = re.sub(
        r"\D",
        "",
        str(
            valor or ""
        ),
    )

    return texto or None


def parse_data(
    valor,
) -> date | None:
    texto = str(
        valor or ""
    ).strip()

    if not texto:
        return None

    for formato in (
        "%Y-%m-%d",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(
                texto[:10],
                formato,
            ).date()

        except ValueError:
            continue

    return None


def parse_inteiro(
    valor,
) -> int | None:
    texto = str(
        valor or ""
    ).strip()

    if not texto:
        return None

    texto = texto.replace(
        " ",
        "",
    )

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

    elif re.fullmatch(
        r"-?\d{1,3}(\.\d{3})+",
        texto,
    ):
        texto = texto.replace(
            ".",
            "",
        )

    try:
        numero = Decimal(
            texto
        )

    except InvalidOperation:
        return None

    if (
        numero
        != numero.to_integral_value()
    ):
        return None

    return int(
        numero
    )


def calcular_circulacao(
    integralizado: int | None,
    tesouraria: int | None,
) -> int | None:

    if (
        integralizado is None
        or tesouraria is None
    ):
        return None

    resultado = (
        integralizado
        - tesouraria
    )

    if resultado < 0:
        return None

    return resultado


def garantir_fontes(
    conn,
) -> None:

    registros = [
        (
            "CVM_DFP_CAPITAL",
            (
                "CVM - DFP "
                "Composição do Capital"
            ),
            (
                "https://dados.cvm.gov.br/"
                "dataset/cia_aberta-doc-dfp"
            ),
            (
                "Quantidade de ações "
                "integralizadas, em tesouraria "
                "e em circulação informada "
                "nas DFP."
            ),
        ),
        (
            "CVM_ITR_CAPITAL",
            (
                "CVM - ITR "
                "Composição do Capital"
            ),
            (
                "https://dados.cvm.gov.br/"
                "dataset/cia_aberta-doc-itr"
            ),
            (
                "Quantidade de ações "
                "integralizadas, em tesouraria "
                "e em circulação informada "
                "nas ITR."
            ),
        ),
        (
            "CVM_CAPITAL",
            (
                "CVM - Composição do Capital "
                "DFP e ITR"
            ),
            "https://dados.cvm.gov.br/",
            (
                "Processo consolidado de "
                "coleta da composição do "
                "capital das companhias "
                "abertas."
            ),
        ),
    ]

    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into
                investimento.fontes_dados (
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
                'FUNDAMENTOS',
                true,
                %s,
                'SEMANAL',
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
            registros,
        )


def carregar_universo(
    conn,
) -> dict[str, dict]:

    with conn.cursor() as cur:
        cur.execute(
            """
            select distinct

                codigo_cvm,

                coalesce(
                    cnpj_cvm_formatado,
                    cnpj_b3
                ) as cnpj

            from
                investimento
                .vw_acoes_validacao_oficial_atual

            where
                elegivel_analise = true

                and codigo_cvm
                    is not null

                and coalesce(
                    cnpj_cvm_formatado,
                    cnpj_b3
                ) is not null
            """
        )

        rows = cur.fetchall()

    universo: dict[
        str,
        dict,
    ] = {}

    for (
        codigo_cvm,
        cnpj,
    ) in rows:

        cnpj_normalizado = (
            normalizar_cnpj(
                cnpj
            )
        )

        if not cnpj_normalizado:
            continue

        universo[
            cnpj_normalizado
        ] = {
            "codigo_cvm":
                normalizar_codigo_cvm(
                    codigo_cvm
                ),

            "cnpj":
                cnpj_normalizado,
        }

    if not universo:
        raise RuntimeError(
            (
                "Universo CVM sem CNPJs "
                "para composição do capital."
            )
        )

    return universo


def baixar_zip(
    documento: str,
    ano: int,
) -> bytes:

    url = URLS[
        documento
    ].format(
        ano=ano,
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "projeto-investimento/1.0 "
            "(coleta automatizada de "
            "dados publicos CVM)"
        ),
        "Accept":
            "*/*",
    }

    ultimo_erro = None

    for tentativa in range(
        1,
        RETRIES + 1,
    ):
        try:
            print(
                (
                    f"Baixando {documento} "
                    f"{ano} - tentativa "
                    f"{tentativa}/{RETRIES}..."
                )
            )

            with requests.get(
                url,
                headers=headers,
                timeout=TIMEOUT,
                stream=True,
            ) as response:

                response.raise_for_status()

                partes = []

                for bloco in (
                    response.iter_content(
                        chunk_size=(
                            1024
                            * 1024
                        ),
                    )
                ):
                    if bloco:
                        partes.append(
                            bloco
                        )

                conteudo = b"".join(
                    partes
                )

            if len(
                conteudo
            ) < 100:

                raise RuntimeError(
                    (
                        f"Arquivo {documento} "
                        f"{ano} vazio "
                        "ou incompleto."
                    )
                )

            return conteudo

        except Exception as exc:
            ultimo_erro = exc

            if tentativa < RETRIES:
                time.sleep(
                    tentativa * 5
                )

    raise RuntimeError(
        (
            f"Falha ao baixar "
            f"{documento} {ano}: "
            f"{ultimo_erro}"
        )
    )


def localizar_arquivo_composicao(
    zip_file: ZipFile,
) -> str | None:

    candidatos = [
        nome
        for nome
        in zip_file.namelist()

        if nome.upper().endswith(
            ".CSV"
        )

        and (
            "COMPOSICAO_CAPITAL"
            in nome.upper()
        )
    ]

    if not candidatos:
        return None

    candidatos.sort(
        key=lambda nome: (
            len(nome),
            nome,
        )
    )

    return candidatos[0]


def processar_csv(
    conteudo: bytes,
    documento: str,
    ano: int,
    universo: dict[str, dict],
) -> tuple[
    int,
    list[dict],
    str | None,
]:

    with ZipFile(
        BytesIO(
            conteudo
        )
    ) as zip_file:

        nome_arquivo = (
            localizar_arquivo_composicao(
                zip_file
            )
        )

        if nome_arquivo is None:
            return (
                0,
                [],
                None,
            )

        with zip_file.open(
            nome_arquivo
        ) as arquivo:

            df = pd.read_csv(
                arquivo,
                sep=";",
                encoding="latin1",
                dtype=str,
                low_memory=False,
            )

    df.columns = [
        str(
            coluna
        ).strip().upper()

        for coluna
        in df.columns
    ]

    lidos = len(
        df
    )

    obrigatorias = {
        "CNPJ_CIA",
        "DT_REFER",
    }

    if not obrigatorias.issubset(
        set(
            df.columns
        )
    ):
        raise CVMCapitalError(
            (
                f"Arquivo {nome_arquivo} "
                "não possui CNPJ_CIA "
                "e DT_REFER."
            )
        )

    df["_CNPJ"] = (
        df[
            "CNPJ_CIA"
        ]
        .fillna("")
        .map(
            normalizar_cnpj
        )
    )

    df = df[
        df["_CNPJ"].isin(
            universo.keys()
        )
    ].copy()

    if df.empty:
        return (
            lidos,
            [],
            nome_arquivo,
        )

    df["_DATA_REF"] = (
        df[
            "DT_REFER"
        ]
        .fillna("")
        .map(
            parse_data
        )
    )

    df = df[
        df["_DATA_REF"].notna()
    ].copy()

    df = df[
        df["_DATA_REF"].map(
            lambda valor:
                valor.year
                == ano
        )
    ].copy()

    if df.empty:
        return (
            lidos,
            [],
            nome_arquivo,
        )

    if "VERSAO" in df.columns:

        df["_VERSAO"] = (
            pd.to_numeric(
                df[
                    "VERSAO"
                ],
                errors="coerce",
            )
            .fillna(
                0
            )
        )

        max_versao = (
            df.groupby(
                [
                    "_CNPJ",
                    "_DATA_REF",
                ]
            )["_VERSAO"]
            .transform(
                "max"
            )
        )

        df = df[
            df["_VERSAO"]
            == max_versao
        ].copy()

    else:
        df["_VERSAO"] = 0

    colunas_quantidade = {
        "qt_acao_ordin_cap_integr":
            "QT_ACAO_ORDIN_CAP_INTEGR",

        "qt_acao_pref_cap_integr":
            "QT_ACAO_PREF_CAP_INTEGR",

        "qt_acao_total_cap_integr":
            "QT_ACAO_TOTAL_CAP_INTEGR",

        "qt_acao_ordin_tesouro":
            "QT_ACAO_ORDIN_TESOURO",

        "qt_acao_pref_tesouro":
            "QT_ACAO_PREF_TESOURO",

        "qt_acao_total_tesouro":
            "QT_ACAO_TOTAL_TESOURO",
    }

    registros: dict[
        tuple[
            str,
            date,
            str,
        ],
        dict,
    ] = {}

    for _, row in (
        df.iterrows()
    ):

        cnpj = row[
            "_CNPJ"
        ]

        empresa = universo.get(
            cnpj
        )

        if not empresa:
            continue

        quantidades = {}

        for (
            destino,
            origem,
        ) in (
            colunas_quantidade.items()
        ):

            quantidades[
                destino
            ] = parse_inteiro(
                (
                    row.get(
                        origem
                    )
                    if origem
                    in df.columns
                    else None
                )
            )

        ordin_circulacao = (
            calcular_circulacao(
                quantidades[
                    "qt_acao_ordin_cap_integr"
                ],
                quantidades[
                    "qt_acao_ordin_tesouro"
                ],
            )
        )

        pref_circulacao = (
            calcular_circulacao(
                quantidades[
                    "qt_acao_pref_cap_integr"
                ],
                quantidades[
                    "qt_acao_pref_tesouro"
                ],
            )
        )

        total_circulacao = (
            calcular_circulacao(
                quantidades[
                    "qt_acao_total_cap_integr"
                ],
                quantidades[
                    "qt_acao_total_tesouro"
                ],
            )
        )

        data_referencia = row[
            "_DATA_REF"
        ]

        registro = {
            "codigo_cvm":
                empresa[
                    "codigo_cvm"
                ],

            "cnpj":
                cnpj,

            "data_referencia":
                data_referencia,

            "versao":
                int(
                    row[
                        "_VERSAO"
                    ]
                ),

            "documento":
                documento,

            **quantidades,

            "qt_acao_ordin_circulacao":
                ordin_circulacao,

            "qt_acao_pref_circulacao":
                pref_circulacao,

            "qt_acao_total_circulacao":
                total_circulacao,

            "fonte":
                FONTES[
                    documento
                ],
        }

        chave = (
            registro[
                "codigo_cvm"
            ],
            data_referencia,
            documento,
        )

        anterior = (
            registros.get(
                chave
            )
        )

        if (
            anterior is None
            or registro[
                "versao"
            ]
            >= anterior[
                "versao"
            ]
        ):
            registros[
                chave
            ] = registro

    return (
        lidos,
        list(
            registros.values()
        ),
        nome_arquivo,
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
    documento: str,
    ano: int,
    registros: list[dict],
) -> int:

    fonte = FONTES[
        documento
    ]

    with conn.cursor() as cur:

        cur.execute(
            """
            delete from
                investimento
                .composicao_capital_cvm

            where
                documento = %s

                and fonte = %s

                and data_referencia
                    >= make_date(
                        %s,
                        1,
                        1
                    )

                and data_referencia
                    < make_date(
                        %s + 1,
                        1,
                        1
                    )
            """,
            (
                documento,
                fonte,
                ano,
                ano,
            ),
        )

        for lote in dividir_em_lotes(
            registros,
            INSERT_BATCH_SIZE,
        ):

            cur.executemany(
                """
                insert into
                    investimento
                    .composicao_capital_cvm (
                        codigo_cvm,
                        cnpj,
                        data_referencia,
                        versao,
                        documento,

                        qt_acao_ordin_cap_integr,
                        qt_acao_pref_cap_integr,
                        qt_acao_total_cap_integr,

                        qt_acao_ordin_tesouro,
                        qt_acao_pref_tesouro,
                        qt_acao_total_tesouro,

                        qt_acao_ordin_circulacao,
                        qt_acao_pref_circulacao,
                        qt_acao_total_circulacao,

                        fonte,
                        coletado_em
                    )

                values (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,

                    %s,
                    %s,
                    %s,

                    %s,
                    %s,
                    %s,

                    %s,
                    %s,
                    %s,

                    %s,
                    now()
                )

                on conflict (
                    codigo_cvm,
                    data_referencia,
                    documento
                )

                do update set

                    cnpj =
                        excluded.cnpj,

                    versao =
                        excluded.versao,

                    qt_acao_ordin_cap_integr =
                        excluded
                        .qt_acao_ordin_cap_integr,

                    qt_acao_pref_cap_integr =
                        excluded
                        .qt_acao_pref_cap_integr,

                    qt_acao_total_cap_integr =
                        excluded
                        .qt_acao_total_cap_integr,

                    qt_acao_ordin_tesouro =
                        excluded
                        .qt_acao_ordin_tesouro,

                    qt_acao_pref_tesouro =
                        excluded
                        .qt_acao_pref_tesouro,

                    qt_acao_total_tesouro =
                        excluded
                        .qt_acao_total_tesouro,

                    qt_acao_ordin_circulacao =
                        excluded
                        .qt_acao_ordin_circulacao,

                    qt_acao_pref_circulacao =
                        excluded
                        .qt_acao_pref_circulacao,

                    qt_acao_total_circulacao =
                        excluded
                        .qt_acao_total_circulacao,

                    fonte =
                        excluded.fonte,

                    coletado_em =
                        now()
                """,
                [
                    (
                        registro[
                            "codigo_cvm"
                        ],

                        registro[
                            "cnpj"
                        ],

                        registro[
                            "data_referencia"
                        ],

                        registro[
                            "versao"
                        ],

                        registro[
                            "documento"
                        ],

                        registro[
                            "qt_acao_ordin_cap_integr"
                        ],

                        registro[
                            "qt_acao_pref_cap_integr"
                        ],

                        registro[
                            "qt_acao_total_cap_integr"
                        ],

                        registro[
                            "qt_acao_ordin_tesouro"
                        ],

                        registro[
                            "qt_acao_pref_tesouro"
                        ],

                        registro[
                            "qt_acao_total_tesouro"
                        ],

                        registro[
                            "qt_acao_ordin_circulacao"
                        ],

                        registro[
                            "qt_acao_pref_circulacao"
                        ],

                        registro[
                            "qt_acao_total_circulacao"
                        ],

                        registro[
                            "fonte"
                        ],
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
    documento: str,
    ano: int,
) -> dict:

    documento = (
        documento
        .upper()
        .strip()
    )

    if documento not in URLS:
        raise ValueError(
            (
                "Documento inválido: "
                f"{documento}"
            )
        )

    if ano < 2020:
        return {
            "documento":
                documento,

            "ano":
                ano,

            "arquivo":
                None,

            "registros_lidos":
                0,

            "registros_gravados":
                0,

            "empresas":
                0,

            "arquivo_ausente":
                True,

            "motivo":
                (
                    "Composição do capital "
                    "não é publicada nos "
                    "ZIPs DFP/ITR "
                    "anteriores a 2020."
                ),
        }

    universo = carregar_universo(
        conn
    )

    conteudo = baixar_zip(
        documento,
        ano,
    )

    (
        lidos,
        registros,
        nome_arquivo,
    ) = processar_csv(
        conteudo,
        documento,
        ano,
        universo,
    )

    # Se o membro esperado não existir
    # ou o CSV estiver vazio, não apagamos
    # carga anterior.
    #
    # Isso protege o histórico contra
    # mudança de layout ou publicação
    # temporariamente incompleta da CVM.
    if (
        nome_arquivo is None
        or lidos == 0
    ):
        gravados = 0

    elif not registros:
        raise CVMCapitalError(
            (
                f"Arquivo {nome_arquivo} "
                "foi lido, mas nenhuma "
                "companhia do universo "
                "foi mapeada. "
                "A carga anterior foi "
                "preservada."
            )
        )

    else:
        gravados = gravar(
            conn,
            documento,
            ano,
            registros,
        )

    return {
        "documento":
            documento,

        "ano":
            ano,

        "arquivo":
            nome_arquivo,

        "registros_lidos":
            lidos,

        "registros_gravados":
            gravados,

        "empresas":
            len(
                {
                    registro[
                        "codigo_cvm"
                    ]
                    for registro
                    in registros
                }
            ),

        "arquivo_ausente":
            nome_arquivo is None,

        "motivo":
            None,
    }
