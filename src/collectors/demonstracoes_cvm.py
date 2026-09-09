from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile

import pandas as pd
import requests


PROCESS_NAME = "demonstracoes_cvm"
SOURCE_LOG_CODE = "CVM_DFP_ITR"

URLS = {
    "DFP": (
        "https://dados.cvm.gov.br/dados/"
        "CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
    ),
    "ITR": (
        "https://dados.cvm.gov.br/dados/"
        "CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{ano}.zip"
    ),
}

FONTES = {
    "DFP": "CVM_DFP",
    "ITR": "CVM_ITR",
}

TIPOS_ACEITOS = {
    "BPA",
    "BPP",
    "DRE",
    "DFC_MD",
    "DFC_MI",
}

# Mantemos somente contas padronizadas necessárias à primeira
# versão do motor fundamentalista.
#
# A ideia é preservar:
# - liquidez;
# - caixa;
# - ativos;
# - dívida;
# - patrimônio líquido;
# - receita;
# - margens;
# - resultado operacional;
# - lucro;
# - fluxo de caixa.
CONTAS_RELEVANTES = {
    "BPA": {
        "1",
        "1.01",
        "1.01.01",
        "1.01.02",
        "1.01.03",
        "1.01.04",
        "1.02",
        "1.02.01",
        "1.02.03",
        "1.02.04",
    },
    "BPP": {
        "2",
        "2.01",
        "2.01.04",
        "2.02",
        "2.02.01",
        "2.03",
        "2.03.01",
        "2.03.02",
        "2.03.04",
        "2.03.05",
    },
    "DRE": {
        "3.01",
        "3.02",
        "3.03",
        "3.04",
        "3.05",
        "3.06",
        "3.07",
        "3.08",
        "3.09",
        "3.11",
    },
    "DFC_MD": {
        "6.01",
        "6.02",
        "6.03",
        "6.04",
        "6.05",
        "6.05.01",
        "6.05.02",
    },
    "DFC_MI": {
        "6.01",
        "6.02",
        "6.03",
        "6.04",
        "6.05",
        "6.05.01",
        "6.05.02",
    },
}

# Algumas contas importantes do fluxo de caixa podem ser
# contas não fixas da companhia. Guardamos somente quando
# a descrição indica algo útil ao motor.
TERMOS_DFC_RELEVANTES = {
    "DEPRECI",
    "AMORTIZ",
    "IMOBILIZAD",
    "INTANGIV",
}


def normalizar_texto(valor) -> str:
    if valor is None:
        return ""

    texto = str(valor).strip().upper()

    texto = unicodedata.normalize(
        "NFKD",
        texto,
    )

    texto = "".join(
        c for c in texto
        if not unicodedata.combining(c)
    )

    return texto


def normalizar_codigo_cvm(valor) -> str:
    texto = re.sub(
        r"\D",
        "",
        str(valor or ""),
    )

    texto = texto.lstrip("0")

    return texto or "0"


def normalizar_cnpj(valor) -> str | None:
    texto = re.sub(
        r"\D",
        "",
        str(valor or ""),
    )

    return texto or None


def parse_data(valor) -> date | None:
    if valor is None:
        return None

    texto = str(valor).strip()

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


def parse_decimal(valor) -> Decimal | None:
    if valor is None:
        return None

    texto = str(valor).strip()

    if not texto:
        return None

    # Suporta tanto:
    # 12345.67
    # quanto
    # 12.345,67
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "")
            texto = texto.replace(",", ".")
        else:
            texto = texto.replace(",", "")

    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return Decimal(texto)

    except InvalidOperation:
        return None


def ajustar_escala(
    valor: Decimal | None,
    escala,
) -> Decimal | None:
    if valor is None:
        return None

    escala_normalizada = normalizar_texto(
        escala,
    )

    if escala_normalizada.startswith("MIL"):
        return valor * Decimal("1000")

    return valor


def conta_relevante(
    tipo: str,
    codigo: str,
    descricao: str,
) -> bool:
    codigo = str(codigo or "").strip()

    if codigo in CONTAS_RELEVANTES.get(
        tipo,
        set(),
    ):
        return True

    if tipo in {
        "DFC_MD",
        "DFC_MI",
    }:
        descricao_normalizada = normalizar_texto(
            descricao,
        )

        return any(
            termo in descricao_normalizada
            for termo in TERMOS_DFC_RELEVANTES
        )

    return False


def garantir_fontes(conn) -> None:
    registros = [
        (
            "CVM_DFP",
            "CVM - DFP Companhias Abertas",
            "FUNDAMENTOS",
            "https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp",
            "SEMANAL",
            "Demonstrações financeiras anuais padronizadas",
        ),
        (
            "CVM_ITR",
            "CVM - ITR Companhias Abertas",
            "FUNDAMENTOS",
            "https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr",
            "SEMANAL",
            "Demonstrações financeiras trimestrais estruturadas",
        ),
        (
            "CVM_DFP_ITR",
            "CVM - Demonstrações DFP e ITR",
            "FUNDAMENTOS",
            "https://dados.cvm.gov.br/",
            "SEMANAL",
            "Coleta normalizada de demonstrações financeiras de companhias abertas",
        ),
    ]

    with conn.cursor() as cur:
        cur.executemany(
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
                %s, %s, %s,
                true,
                %s, %s, %s,
                true
            )
            on conflict (codigo)
            do update set
                nome = excluded.nome,
                tipo = excluded.tipo,
                oficial = true,
                url_base = excluded.url_base,
                periodicidade = excluded.periodicidade,
                finalidade = excluded.finalidade,
                ativa = true,
                atualizado_em = now()
            """,
            registros,
        )


def carregar_universo(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
                ativo_id,
                codigo_cvm,
                coalesce(
                    cnpj_cvm_formatado,
                    cnpj_b3
                ) as cnpj
            from investimento.vw_acoes_validacao_oficial_atual
            where codigo_cvm is not null
            """
        )

        rows = cur.fetchall()

    universo = {}

    for ativo_id, codigo_cvm, cnpj in rows:
        codigo_normalizado = normalizar_codigo_cvm(
            codigo_cvm,
        )

        universo[codigo_normalizado] = {
            "ativo_id": ativo_id,
            "codigo_cvm": str(codigo_cvm),
            "cnpj": normalizar_cnpj(cnpj),
        }

    return universo


def baixar_zip(
    documento: str,
    ano: int,
) -> bytes:
    url = URLS[documento].format(
        ano=ano,
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 projeto-investimento/1.0 "
            "(coleta automatizada de dados publicos CVM)"
        ),
        "Accept": "*/*",
    }

    ultimo_erro = None

    for tentativa in range(1, 6):
        try:
            print(
                f"Baixando {documento} {ano} "
                f"- tentativa {tentativa}/5..."
            )

            with requests.get(
                url,
                headers=headers,
                timeout=(30, 300),
                stream=True,
            ) as response:
                response.raise_for_status()

                partes = []

                for bloco in response.iter_content(
                    chunk_size=1024 * 1024,
                ):
                    if bloco:
                        partes.append(bloco)

                conteudo = b"".join(partes)

            if len(conteudo) < 100:
                raise RuntimeError(
                    f"Arquivo {documento} {ano} vazio "
                    "ou incompleto."
                )

            return conteudo

        except Exception as exc:
            ultimo_erro = exc

            if tentativa < 5:
                time.sleep(
                    tentativa * 5,
                )

    raise RuntimeError(
        f"Falha ao baixar {documento} {ano}: "
        f"{ultimo_erro}"
    )


def identificar_arquivo(
    nome: str,
) -> tuple[str, bool] | None:
    nome_upper = nome.upper()

    if not nome_upper.endswith(".CSV"):
        return None

    tipo = None

    for candidato in sorted(
        TIPOS_ACEITOS,
        key=len,
        reverse=True,
    ):
        if f"_{candidato}_" in nome_upper:
            tipo = candidato
            break

    if tipo is None:
        return None

    if "_CON_" in nome_upper:
        consolidado = True
    elif "_IND_" in nome_upper:
        consolidado = False
    else:
        return None

    return (
        tipo,
        consolidado,
    )


def processar_csv(
    conteudo: bytes,
    nome_arquivo: str,
    documento: str,
    ano: int,
    tipo: str,
    consolidado: bool,
    universo: dict[str, dict],
) -> tuple[int, list[dict]]:
    df = pd.read_csv(
        BytesIO(conteudo),
        sep=";",
        encoding="latin1",
        dtype=str,
        low_memory=False,
    )

    df.columns = [
        str(coluna).strip().upper()
        for coluna in df.columns
    ]

    lidos = len(df)

    colunas_obrigatorias = {
        "CD_CVM",
        "DT_REFER",
        "CD_CONTA",
        "VL_CONTA",
    }

    if not colunas_obrigatorias.issubset(
        set(df.columns),
    ):
        return lidos, []

    df["_COD_CVM"] = (
        df["CD_CVM"]
        .fillna("")
        .map(normalizar_codigo_cvm)
    )

    df = df[
        df["_COD_CVM"].isin(
            universo.keys(),
        )
    ].copy()

    if df.empty:
        return lidos, []

    # Mantém apenas o exercício atual.
    # A CVM também fornece a coluna comparativa
    # do exercício anterior.
    if "ORDEM_EXERC" in df.columns:
        ordem = (
            df["ORDEM_EXERC"]
            .fillna("")
            .map(normalizar_texto)
        )

        mascara_ultimo = ordem.str.contains(
            "ULTIMO",
            regex=False,
        )

        if mascara_ultimo.any():
            df = df[
                mascara_ultimo
            ].copy()

    # A CVM pode manter reapresentações.
    # Mantemos somente a maior versão para
    # companhia + data de referência.
    if "VERSAO" in df.columns:
        df["_VERSAO"] = pd.to_numeric(
            df["VERSAO"],
            errors="coerce",
        ).fillna(0)

        max_versao = (
            df.groupby(
                [
                    "_COD_CVM",
                    "DT_REFER",
                ]
            )["_VERSAO"]
            .transform("max")
        )

        df = df[
            df["_VERSAO"] == max_versao
        ].copy()

    else:
        df["_VERSAO"] = 0

    df["_DATA_REF"] = (
        df["DT_REFER"]
        .fillna("")
        .map(parse_data)
    )

    df = df[
        df["_DATA_REF"].notna()
    ].copy()

    df = df[
        df["_DATA_REF"].map(
            lambda x: x.year == ano
        )
    ].copy()

    if df.empty:
        return lidos, []

    registros = []

    for _, row in df.iterrows():
        codigo_conta = str(
            row.get(
                "CD_CONTA",
                "",
            )
            or ""
        ).strip()

        descricao = str(
            row.get(
                "DS_CONTA",
                "",
            )
            or ""
        ).strip()

        if not conta_relevante(
            tipo,
            codigo_conta,
            descricao,
        ):
            continue

        cod_normalizado = row[
            "_COD_CVM"
        ]

        ativo = universo.get(
            cod_normalizado,
        )

        if not ativo:
            continue

        valor = parse_decimal(
            row.get("VL_CONTA"),
        )

        valor = ajustar_escala(
            valor,
            row.get(
                "ESCALA_MOEDA",
            ),
        )

        if valor is None:
            continue

        data_inicio = parse_data(
            row.get(
                "DT_INI_EXERC",
            )
        )

        cnpj = normalizar_cnpj(
            row.get(
                "CNPJ_CIA",
            )
        )

        if not cnpj:
            cnpj = ativo[
                "cnpj"
            ]

        versao = int(
            row.get(
                "_VERSAO",
                0,
            )
            or 0
        )

        registros.append(
            {
                "ativo_id": ativo[
                    "ativo_id"
                ],
                "cnpj": cnpj,
                "codigo_cvm": ativo[
                    "codigo_cvm"
                ],
                "data_referencia": row[
                    "_DATA_REF"
                ],
                "data_inicio_periodo": data_inicio,
                "tipo_demonstracao": tipo,
                "codigo_conta": codigo_conta,
                "descricao_conta": descricao,
                "valor": valor,
                "moeda": (
                    row.get(
                        "MOEDA"
                    )
                    or "BRL"
                ),
                "consolidado": consolidado,
                "documento": (
                    f"{documento}:{ano}:"
                    f"v{versao}:"
                    f"{nome_arquivo}"
                ),
                "fonte": FONTES[
                    documento
                ],
            }
        )

    return (
        lidos,
        registros,
    )


def preferir_consolidado(
    registros: list[dict],
) -> list[dict]:
    grupos_consolidados = {
        (
            r["ativo_id"],
            r["data_referencia"],
            r["tipo_demonstracao"],
        )
        for r in registros
        if r["consolidado"]
    }

    filtrados = []

    for registro in registros:
        grupo = (
            registro["ativo_id"],
            registro["data_referencia"],
            registro[
                "tipo_demonstracao"
            ],
        )

        if (
            not registro["consolidado"]
            and grupo in grupos_consolidados
        ):
            continue

        filtrados.append(
            registro,
        )

    # Segurança contra duplicações dentro
    # de um mesmo arquivo/reapresentação.
    unicos = {}

    for registro in filtrados:
        chave = (
            registro["ativo_id"],
            registro[
                "data_referencia"
            ],
            registro[
                "data_inicio_periodo"
            ],
            registro[
                "tipo_demonstracao"
            ],
            registro[
                "codigo_conta"
            ],
            registro[
                "fonte"
            ],
        )

        unicos[chave] = registro

    return list(
        unicos.values()
    )


def extrair_documento(
    zip_bytes: bytes,
    documento: str,
    ano: int,
    universo: dict[str, dict],
) -> tuple[int, list[dict]]:
    total_lidos = 0
    registros = []

    with ZipFile(
        BytesIO(zip_bytes)
    ) as arquivo_zip:

        for nome in arquivo_zip.namelist():
            identificacao = identificar_arquivo(
                nome,
            )

            if identificacao is None:
                continue

            tipo, consolidado = identificacao

            conteudo = arquivo_zip.read(
                nome,
            )

            lidos, linhas = processar_csv(
                conteudo=conteudo,
                nome_arquivo=nome,
                documento=documento,
                ano=ano,
                tipo=tipo,
                consolidado=consolidado,
                universo=universo,
            )

            total_lidos += lidos
            registros.extend(
                linhas,
            )

    registros = preferir_consolidado(
        registros,
    )

    return (
        total_lidos,
        registros,
    )


def gravar_documento(
    conn,
    documento: str,
    ano: int,
    registros: list[dict],
) -> int:
    fonte = FONTES[
        documento
    ]

    inicio = date(
        ano,
        1,
        1,
    )

    fim = date(
        ano + 1,
        1,
        1,
    )

    with conn.cursor() as cur:
        # A carga do ano é substitutiva.
        # Isso torna a rotina idempotente e
        # absorve reapresentações da CVM.
        cur.execute(
            """
            delete from investimento.demonstracoes_financeiras
            where fonte = %s
              and data_referencia >= %s
              and data_referencia < %s
            """,
            (
                fonte,
                inicio,
                fim,
            ),
        )

        if registros:
            cur.executemany(
                """
                insert into investimento.demonstracoes_financeiras (
                    ativo_id,
                    cnpj,
                    codigo_cvm,
                    data_referencia,
                    data_inicio_periodo,
                    tipo_demonstracao,
                    codigo_conta,
                    descricao_conta,
                    valor,
                    moeda,
                    consolidado,
                    documento,
                    publicado_em,
                    fonte
                )
                values (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    null, %s
                )
                """,
                [
                    (
                        r["ativo_id"],
                        r["cnpj"],
                        r["codigo_cvm"],
                        r[
                            "data_referencia"
                        ],
                        r[
                            "data_inicio_periodo"
                        ],
                        r[
                            "tipo_demonstracao"
                        ],
                        r["codigo_conta"],
                        r[
                            "descricao_conta"
                        ],
                        r["valor"],
                        r["moeda"],
                        r["consolidado"],
                        r["documento"],
                        r["fonte"],
                    )
                    for r in registros
                ],
            )

    return len(
        registros
    )


def coletar(
    conn,
    ano: int,
    documento: str = "AMBOS",
) -> tuple[int, int]:
    documento = documento.upper()

    if documento == "AMBOS":
        documentos = [
            "DFP",
            "ITR",
        ]

    elif documento in {
        "DFP",
        "ITR",
    }:
        documentos = [
            documento,
        ]

    else:
        raise ValueError(
            "documento deve ser DFP, ITR ou AMBOS"
        )

    universo = carregar_universo(
        conn,
    )

    if not universo:
        raise RuntimeError(
            "Universo de ações vazio."
        )

    print(
        f"Universo CVM carregado: "
        f"{len(universo)} companhias."
    )

    total_lidos = 0
    total_gravados = 0

    for doc in documentos:
        zip_bytes = baixar_zip(
            doc,
            ano,
        )

        lidos, registros = extrair_documento(
            zip_bytes=zip_bytes,
            documento=doc,
            ano=ano,
            universo=universo,
        )

        gravados = gravar_documento(
            conn=conn,
            documento=doc,
            ano=ano,
            registros=registros,
        )

        total_lidos += lidos
        total_gravados += gravados

        print(
            f"{doc} {ano}: "
            f"lidos={lidos} | "
            f"gravados={gravados}"
        )

    return (
        total_lidos,
        total_gravados,
    )
