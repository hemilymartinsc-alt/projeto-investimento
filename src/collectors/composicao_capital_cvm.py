from __future__ import annotations

import base64
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import quote
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

B3_API_BASE = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "listedCompaniesProxy/CompanyCall"
)

B3_SUPPLEMENT_URL = (
    f"{B3_API_BASE}/GetListedSupplementCompany"
)

TIMEOUT = (
    30,
    300,
)

B3_TIMEOUT = (
    20,
    90,
)

RETRIES = 5
REQUEST_DELAY = 0.12
INSERT_BATCH_SIZE = 1000

FATORES_ESCALA = (
    1,
    1000,
    1_000_000,
)

LIMITE_VALIDACAO_B3 = Decimal(
    "0.25"
)


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
    ).lstrip(
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
    ).strip().replace(
        " ",
        "",
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
            texto = (
                texto
                .replace(
                    ".",
                    "",
                )
                .replace(
                    ",",
                    ".",
                )
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


def multiplicar(
    valor: int | None,
    fator: int,
) -> int | None:

    if valor is None:
        return None

    return valor * fator


def codificar_payload_b3(
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


def criar_session_b3() -> requests.Session:

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
            "Referer":
                "https://www.b3.com.br/",
        }
    )

    return session


def requisitar_b3(
    session: requests.Session,
    codigo_emissor: str,
) -> dict:

    payload = {
        "issuingCompany":
            codigo_emissor,

        "language":
            "pt-br",
    }

    url = (
        f"{B3_SUPPLEMENT_URL}/"
        f"{codificar_payload_b3(payload)}"
    )

    ultimo_erro = None

    for tentativa in range(
        1,
        RETRIES + 1,
    ):
        try:

            resposta = session.get(
                url,
                timeout=B3_TIMEOUT,
            )

            resposta.raise_for_status()

            dados = resposta.json()

            if not isinstance(
                dados,
                dict,
            ):
                raise CVMCapitalError(
                    (
                        "Resposta B3 inválida "
                        f"para {codigo_emissor}."
                    )
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

    raise CVMCapitalError(
        (
            "Falha ao consultar a B3 "
            f"para {codigo_emissor}: "
            f"{ultimo_erro}"
        )
    )


def localizar_info_b3(
    objeto,
) -> dict | None:

    if isinstance(
        objeto,
        dict,
    ):
        if any(
            chave in objeto
            for chave in (
                "totalNumberShares",
                "numberCommonShares",
                "numberPreferredShares",
            )
        ):
            return objeto

        for valor in objeto.values():

            encontrado = (
                localizar_info_b3(
                    valor
                )
            )

            if encontrado is not None:
                return encontrado

    elif isinstance(
        objeto,
        list,
    ):
        for item in objeto:

            encontrado = (
                localizar_info_b3(
                    item
                )
            )

            if encontrado is not None:
                return encontrado

    return None


def obter_acoes_b3(
    session: requests.Session,
    codigo_emissor: str | None,
) -> dict | None:

    if not codigo_emissor:
        return None

    dados = requisitar_b3(
        session,
        codigo_emissor,
    )

    info = localizar_info_b3(
        dados
    )

    if info is None:
        return None

    ordinarias = parse_inteiro(
        info.get(
            "numberCommonShares"
        )
    )

    preferenciais = parse_inteiro(
        info.get(
            "numberPreferredShares"
        )
    )

    total = parse_inteiro(
        info.get(
            "totalNumberShares"
        )
    )

    if (
        total is None
        and (
            ordinarias is not None
            or preferenciais is not None
        )
    ):
        total = (
            (ordinarias or 0)
            + (preferenciais or 0)
        )

    if (
        total is None
        or total <= 0
    ):
        return None

    return {
        "ordinarias":
            ordinarias,

        "preferenciais":
            preferenciais,

        "total":
            total,
    }


def escolher_fator_escala(
    total_cvm: int | None,
    total_b3: int | None,
) -> tuple[
    int,
    Decimal | None,
    bool,
]:

    if (
        total_cvm is None
        or total_cvm <= 0
        or total_b3 is None
        or total_b3 <= 0
    ):
        return (
            1,
            None,
            False,
        )

    melhor_fator = 1
    melhor_erro = None

    for fator in FATORES_ESCALA:

        normalizado = (
            Decimal(
                total_cvm
            )
            * Decimal(
                fator
            )
        )

        erro = (
            abs(
                normalizado
                - Decimal(
                    total_b3
                )
            )
            / Decimal(
                total_b3
            )
        )

        if (
            melhor_erro is None
            or erro < melhor_erro
        ):
            melhor_fator = fator
            melhor_erro = erro

    validado = bool(
        melhor_erro is not None
        and melhor_erro
        <= LIMITE_VALIDACAO_B3
    )

    return (
        melhor_fator,
        melhor_erro,
        validado,
    )


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
) -> dict[
    str,
    dict,
]:

    with conn.cursor() as cur:

        cur.execute(
            """
            select distinct

                v.codigo_cvm,

                coalesce(
                    v.cnpj_cvm_formatado,
                    v.cnpj_b3
                ) as cnpj,

                b.codigo_emissor

            from
                investimento
                .vw_acoes_validacao_oficial_atual v

            left join
                investimento.b3_empresas_listadas b

              on ltrim(
                    b.codigo_cvm,
                    '0'
                 )
                 =
                 ltrim(
                    v.codigo_cvm,
                    '0'
                 )

            where
                v.elegivel_analise = true

                and v.codigo_cvm
                    is not null

                and coalesce(
                    v.cnpj_cvm_formatado,
                    v.cnpj_b3
                ) is not null
            """
        )

        rows = cur.fetchall()

    universo = {}

    for (
        codigo_cvm,
        cnpj,
        codigo_emissor,
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

            "codigo_emissor":
                (
                    str(
                        codigo_emissor
                    ).strip()

                    if codigo_emissor
                    else None
                ),
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

                partes = [
                    bloco

                    for bloco
                    in response.iter_content(
                        chunk_size=(
                            1024
                            * 1024
                        )
                    )

                    if bloco
                ]

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
                "sem CNPJ_CIA/DT_REFER."
            )
        )

    df[
        "_CNPJ"
    ] = (
        df[
            "CNPJ_CIA"
        ]
        .fillna("")
        .map(
            normalizar_cnpj
        )
    )

    df = df[
        df[
            "_CNPJ"
        ].isin(
            universo.keys()
        )
    ].copy()

    if df.empty:
        return (
            lidos,
            [],
            nome_arquivo,
        )

    df[
        "_DATA_REF"
    ] = (
        df[
            "DT_REFER"
        ]
        .fillna("")
        .map(
            parse_data
        )
    )

    df = df[
        df[
            "_DATA_REF"
        ].notna()
    ].copy()

    df = df[
        df[
            "_DATA_REF"
        ].map(
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

        df[
            "_VERSAO"
        ] = pd.to_numeric(
            df[
                "VERSAO"
            ],
            errors="coerce",
        ).fillna(
            0
        )

        max_versao = (
            df.groupby(
                [
                    "_CNPJ",
                    "_DATA_REF",
                ]
            )[
                "_VERSAO"
            ]
            .transform(
                "max"
            )
        )

        df = df[
            df[
                "_VERSAO"
            ]
            == max_versao
        ].copy()

    else:
        df[
            "_VERSAO"
        ] = 0

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

    registros = {}

    for _, row in (
        df.iterrows()
    ):

        cnpj = row[
            "_CNPJ"
        ]

        empresa = (
            universo.get(
                cnpj
            )
        )

        if not empresa:
            continue

        quantidades = {
            destino:
                parse_inteiro(
                    (
                        row.get(
                            origem
                        )

                        if origem
                        in df.columns
                        else None
                    )
                )

            for (
                destino,
                origem,
            )
            in colunas_quantidade.items()
        }

        registro = {
            "codigo_cvm":
                empresa[
                    "codigo_cvm"
                ],

            "cnpj":
                cnpj,

            "codigo_emissor":
                empresa[
                    "codigo_emissor"
                ],

            "data_referencia":
                row[
                    "_DATA_REF"
                ],

            "versao":
                int(
                    row[
                        "_VERSAO"
                    ]
                ),

            "documento":
                documento,

            **quantidades,

            "fonte":
                FONTES[
                    documento
                ],
        }

        chave = (
            registro[
                "codigo_cvm"
            ],
            registro[
                "data_referencia"
            ],
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


def normalizar_com_b3(
    registros: list[dict],
) -> dict:

    session = criar_session_b3()

    cache = {}

    stats = {
        "empresas_consultadas_b3":
            0,

        "empresas_com_acoes_b3":
            0,

        "linhas_fator_1":
            0,

        "linhas_fator_1000":
            0,

        "linhas_fator_1000000":
            0,

        "linhas_validadas_b3":
            0,

        "linhas_sem_ancora_b3":
            0,
    }

    try:

        for registro in registros:

            codigo_emissor = (
                registro.get(
                    "codigo_emissor"
                )
            )

            if (
                codigo_emissor
                and codigo_emissor
                not in cache
            ):

                stats[
                    "empresas_consultadas_b3"
                ] += 1

                try:

                    cache[
                        codigo_emissor
                    ] = obter_acoes_b3(
                        session,
                        codigo_emissor,
                    )

                except Exception as exc:

                    print(
                        (
                            "Aviso: B3 sem âncora "
                            f"para {codigo_emissor}: "
                            f"{exc}"
                        )
                    )

                    cache[
                        codigo_emissor
                    ] = None

            info_b3 = (
                cache.get(
                    codigo_emissor
                )

                if codigo_emissor
                else None
            )

            total_cvm = registro.get(
                "qt_acao_total_cap_integr"
            )

            total_b3 = (
                info_b3[
                    "total"
                ]

                if info_b3
                else None
            )

            (
                fator,
                divergencia,
                validado,
            ) = escolher_fator_escala(
                total_cvm,
                total_b3,
            )

            for campo in (
                "qt_acao_ordin_cap_integr",
                "qt_acao_pref_cap_integr",
                "qt_acao_total_cap_integr",
                "qt_acao_ordin_tesouro",
                "qt_acao_pref_tesouro",
                "qt_acao_total_tesouro",
            ):

                registro[
                    campo
                ] = multiplicar(
                    registro.get(
                        campo
                    ),
                    fator,
                )

            registro[
                "qt_acao_ordin_circulacao"
            ] = calcular_circulacao(
                registro.get(
                    "qt_acao_ordin_cap_integr"
                ),
                registro.get(
                    "qt_acao_ordin_tesouro"
                ),
            )

            registro[
                "qt_acao_pref_circulacao"
            ] = calcular_circulacao(
                registro.get(
                    "qt_acao_pref_cap_integr"
                ),
                registro.get(
                    "qt_acao_pref_tesouro"
                ),
            )

            registro[
                "qt_acao_total_circulacao"
            ] = calcular_circulacao(
                registro.get(
                    "qt_acao_total_cap_integr"
                ),
                registro.get(
                    "qt_acao_total_tesouro"
                ),
            )

            registro[
                "fator_escala_aplicado"
            ] = fator

            registro[
                "total_acoes_b3"
            ] = total_b3

            registro[
                "divergencia_relativa_b3"
            ] = divergencia

            registro[
                "validado_b3"
            ] = validado

            stats[
                f"linhas_fator_{fator}"
            ] += 1

            if validado:
                stats[
                    "linhas_validadas_b3"
                ] += 1

            if total_b3 is None:
                stats[
                    "linhas_sem_ancora_b3"
                ] += 1

    finally:

        session.close()

    stats[
        "empresas_com_acoes_b3"
    ] = sum(
        1

        for valor
        in cache.values()

        if valor is not None
    )

    return stats


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
                investimento.composicao_capital_cvm

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
                    investimento.composicao_capital_cvm (
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

                        fator_escala_aplicado,
                        total_acoes_b3,
                        divergencia_relativa_b3,
                        validado_b3,

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

                    fator_escala_aplicado =
                        excluded
                        .fator_escala_aplicado,

                    total_acoes_b3 =
                        excluded
                        .total_acoes_b3,

                    divergencia_relativa_b3 =
                        excluded
                        .divergencia_relativa_b3,

                    validado_b3 =
                        excluded
                        .validado_b3,

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
                            "fator_escala_aplicado"
                        ],

                        registro[
                            "total_acoes_b3"
                        ],

                        registro[
                            "divergencia_relativa_b3"
                        ],

                        registro[
                            "validado_b3"
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

            "normalizacao_b3":
                {},
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

    normalizacao_b3 = {}

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

        normalizacao_b3 = (
            normalizar_com_b3(
                registros
            )
        )

        print(
            (
                "Normalização B3: "
                f"{normalizacao_b3}"
            )
        )

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

        "normalizacao_b3":
            normalizacao_b3,
    }
