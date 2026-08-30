from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

import requests

from src.database import connect


SOURCE_CODE = "CVM_FUNDOS_CAD"
PROCESS_NAME = "coletar_cadastro_fundos_cvm"
DATA_ARCHIVE = "registro_fundo_classe.zip"
LEGACY_DATA_FILE = "cad_fi.csv"
METADATA_ARCHIVE = "meta_registro_fundo_classe.zip"
LEGACY_METADATA_FILE = "meta_cad_fi.txt"
OFFICIAL_URLS = {
    DATA_ARCHIVE: (
        "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/"
        "registro_fundo_classe.zip"
    ),
    LEGACY_DATA_FILE: (
        "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/cad_fi.csv"
    ),
    METADATA_ARCHIVE: (
        "https://dados.cvm.gov.br/dados/FI/CAD/META/"
        "meta_registro_fundo_classe.zip"
    ),
    LEGACY_METADATA_FILE: (
        "https://dados.cvm.gov.br/dados/FI/CAD/META/meta_cad_fi.txt"
    ),
}

CSV_ENCODING = "latin-1"
HTTP_TIMEOUT = (5, 30)
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF_SECONDS = 1

# Limites iniciais conservadores. A comparação percentual deve ser
# recalibrada somente após acumular histórico real do cadastro oficial.
MIN_TOTAL_RECORDS = 50_000
MIN_FUND_RECORDS = 10_000
MIN_IDENTIFIER_COVERAGE = 0.95
MIN_CNPJ_COVERAGE = 0.90
MAX_TOTAL_VARIATION_RATIO = 0.20

DATA_MEMBERS = {
    "FUNDO": "registro_fundo.csv",
    "CLASSE": "registro_classe.csv",
    "SUBCLASSE": "registro_subclasse.csv",
}
METADATA_MEMBERS = {
    "FUNDO": "meta_registro_fundo.txt",
    "CLASSE": "meta_registro_classe.txt",
    "SUBCLASSE": "meta_registro_subclasse.txt",
}

FUND_HEADERS = (
    "ID_Registro_Fundo",
    "CNPJ_Fundo",
    "Codigo_CVM",
    "Data_Registro",
    "Data_Constituicao",
    "Tipo_Fundo",
    "Denominacao_Social",
    "Data_Cancelamento",
    "Situacao",
    "Data_Inicio_Situacao",
    "Data_Adaptacao_RCVM175",
    "Data_Inicio_Exercicio_Social",
    "Data_Fim_Exercicio_Social",
    "Patrimonio_Liquido",
    "Data_Patrimonio_Liquido",
    "Diretor",
    "CNPJ_Administrador",
    "Administrador",
    "Tipo_Pessoa_Gestor",
    "CPF_CNPJ_Gestor",
    "Gestor",
)
CLASS_HEADERS = (
    "ID_Registro_Fundo",
    "ID_Registro_Classe",
    "CNPJ_Classe",
    "Codigo_CVM",
    "Data_Registro",
    "Data_Constituicao",
    "Data_Inicio",
    "Tipo_Classe",
    "Denominacao_Social",
    "Situacao",
    "Data_Inicio_Situacao",
    "Classificacao",
    "Indicador_Desempenho",
    "Classe_Cotas",
    "Classificacao_Anbima",
    "Tributacao_Longo_Prazo",
    "Entidade_Investimento",
    "Permitido_Aplicacao_CemPorCento_Exterior",
    "Classe_ESG",
    "Forma_Condominio",
    "Exclusivo",
    "Publico_Alvo",
    "Patrimonio_Liquido",
    "Data_Patrimonio_Liquido",
    "CNPJ_Auditor",
    "Auditor",
    "CNPJ_Custodiante",
    "Custodiante",
    "CNPJ_Controlador",
    "Controlador",
)
SUBCLASS_HEADERS = (
    "ID_Registro_Classe",
    "ID_Subclasse",
    "Codigo_CVM",
    "Data_Constituicao",
    "Data_Inicio",
    "Denominacao_Social",
    "Situacao",
    "Data_Inicio_Situacao",
    "Forma_Condominio",
    "Exclusivo",
    "Publico_Alvo",
    "Previdenciario",
    "Exclusivo_INR",
    "Exclusivo_Previdencia_Complementar",
)
LEGACY_HEADERS = (
    "TP_FUNDO",
    "CNPJ_FUNDO",
    "DENOM_SOCIAL",
    "DT_REG",
    "DT_CONST",
    "CD_CVM",
    "DT_CANCEL",
    "SIT",
    "DT_INI_SIT",
    "DT_INI_ATIV",
    "DT_INI_EXERC",
    "DT_FIM_EXERC",
    "CLASSE",
    "DT_INI_CLASSE",
    "RENTAB_FUNDO",
    "CONDOM",
    "FUNDO_COTAS",
    "FUNDO_EXCLUSIVO",
    "TRIB_LPRAZO",
    "PUBLICO_ALVO",
    "ENTID_INVEST",
    "TAXA_PERFM",
    "INF_TAXA_PERFM",
    "TAXA_ADM",
    "INF_TAXA_ADM",
    "VL_PATRIM_LIQ",
    "DT_PATRIM_LIQ",
    "DIRETOR",
    "CNPJ_ADMIN",
    "ADMIN",
    "PF_PJ_GESTOR",
    "CPF_CNPJ_GESTOR",
    "GESTOR",
    "CNPJ_AUDITOR",
    "AUDITOR",
    "CNPJ_CUSTODIANTE",
    "CUSTODIANTE",
    "CNPJ_CONTROLADOR",
    "CONTROLADOR",
    "INVEST_CEMPR_EXTER",
    "CLASSE_ANBIMA",
)
REQUIRED_HEADERS = {
    "FUNDO": FUND_HEADERS,
    "CLASSE": CLASS_HEADERS,
    "SUBCLASSE": SUBCLASS_HEADERS,
    "LEGADO": LEGACY_HEADERS,
}

CVM_COPY_COLUMNS = (
    "fonte_codigo",
    "fonte_arquivo",
    "regime_regulatorio",
    "tipo_registro",
    "identificador_oficial",
    "id_registro_fundo",
    "id_registro_classe",
    "id_subclasse",
    "codigo_cvm",
    "cnpj_registro_normalizado",
    "cnpj_fundo_normalizado",
    "cnpj_classe_normalizado",
    "denominacao_social",
    "denominacao_normalizada",
    "situacao",
    "tipo_fundo",
    "tipo_classe",
    "categoria_regulatoria",
    "classificacao_anbima",
    "data_registro",
    "data_constituicao",
    "data_inicio_atividade",
    "data_inicio_situacao",
    "data_cancelamento",
    "data_adaptacao_rcvm175",
    "administrador_cnpj_normalizado",
    "administrador_nome",
    "gestor_documento_normalizado",
    "gestor_nome",
    "data_referencia",
    "dados_raw",
)

B3_FUNDS_SQL = """
select *
from investimento.ativos
where classe = 'FUNDO'
  and instrumento_canonico = true
  and atividade_confirmada_b3 = true
"""

AUDIT_TICKERS = ("HGLG11", "KNRI11", "AFHI11", "AAGR11", "AAZQ11")
GENERIC_MATCH_TOKENS = frozenset(
    {
        "A",
        "AS",
        "DA",
        "DAS",
        "DE",
        "DO",
        "DOS",
        "E",
        "EM",
        "FUNDO",
        "FUNDOS",
        "INVESTIMENTO",
        "INVESTIMENTOS",
    }
)
FORMAL_NAME_EXPANSIONS = {
    "FDO": ("FUNDO",),
    "FDOS": ("FUNDOS",),
    "INV": ("INVESTIMENTO",),
    "IMOB": ("IMOBILIARIO",),
    "RESP": ("RESPONSABILIDADE",),
    "LIM": ("LIMITADA",),
    "LTDA": ("LIMITADA",),
    "RL": ("RESPONSABILIDADE", "LIMITADA"),
}


class CvmFundsError(RuntimeError):
    """Falha fechada de download, layout, sanidade ou processamento CVM."""


@dataclass(frozen=True)
class DownloadedArtifact:
    name: str
    url: str
    content: bytes
    last_modified: datetime | None


def progress(message: str) -> None:
    print(f"CVM fundos: {message}", flush=True)


def clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_document(value) -> str | None:
    text = clean(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) in {11, 14} else None


def normalize_cnpj(value) -> str | None:
    document = normalize_document(value)
    return document if document and len(document) == 14 else None


def normalize_name(value) -> str:
    text = clean(value) or ""
    text = unicodedata.normalize("NFKD", text.upper())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    tokens: list[str] = []
    for token in text.split():
        tokens.extend(FORMAL_NAME_EXPANSIONS.get(token, (token,)))
    return " ".join(tokens)


def parse_date(value, *, field: str, source: str) -> date | None:
    text = clean(value)
    if not text:
        return None
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], date_format).date()
        except ValueError:
            continue
    raise CvmFundsError(
        f"data inválida no arquivo {source}: campo={field} valor={text!r}"
    )


def parse_last_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def download_artifact(name: str, url: str) -> DownloadedArtifact:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            progress(
                f"baixando fonte oficial: arquivo={name} tentativa={attempt}"
            )
            response = requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": (
                        "projeto-investimento/1.0 "
                        "(coleta de dados publicos CVM)"
                    )
                },
            )
            response.raise_for_status()
            if not response.content:
                raise CvmFundsError(f"download vazio: arquivo={name}")
            return DownloadedArtifact(
                name=name,
                url=url,
                content=response.content,
                last_modified=parse_last_modified(
                    response.headers.get("Last-Modified")
                ),
            )
        except (requests.RequestException, CvmFundsError) as exc:
            last_error = exc
            if attempt == DOWNLOAD_ATTEMPTS:
                break
            time.sleep(DOWNLOAD_BACKOFF_SECONDS * attempt)
    raise CvmFundsError(
        f"falha no download oficial após {DOWNLOAD_ATTEMPTS} tentativas: "
        f"arquivo={name} erro={last_error}"
    ) from last_error


def download_official_artifacts() -> dict[str, DownloadedArtifact]:
    return {
        name: download_artifact(name, url)
        for name, url in OFFICIAL_URLS.items()
    }


def inspect_zip(
    content: bytes,
    expected_members: dict[str, str],
    archive_name: str,
) -> dict[str, str]:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member:
                raise CvmFundsError(
                    f"ZIP corrompido: arquivo={archive_name} "
                    f"membro={corrupt_member}"
                )
            names_by_basename: dict[str, list[str]] = defaultdict(list)
            for member in archive.namelist():
                if member.endswith("/"):
                    continue
                basename = PurePosixPath(member).name.lower()
                names_by_basename[basename].append(member)
    except BadZipFile as exc:
        raise CvmFundsError(f"ZIP inválido: arquivo={archive_name}") from exc

    detected: dict[str, str] = {}
    for record_type, expected_name in expected_members.items():
        candidates = names_by_basename.get(expected_name.lower(), [])
        if len(candidates) != 1:
            raise CvmFundsError(
                "membro oficial ausente ou duplicado: "
                f"arquivo={archive_name} esperado={expected_name} "
                f"encontrados={len(candidates)}"
            )
        detected[record_type] = candidates[0]
    return detected


def validate_headers(
    actual_headers: list[str] | None,
    required_headers: tuple[str, ...],
    source: str,
) -> None:
    if not actual_headers:
        raise CvmFundsError(f"arquivo CSV sem cabeçalho: arquivo={source}")
    missing = [field for field in required_headers if field not in actual_headers]
    if missing:
        raise CvmFundsError(
            "layout oficial alterado; carga bloqueada: "
            f"arquivo={source} colunas_obrigatorias_ausentes={missing}"
        )


def parse_csv_content(
    content: bytes,
    *,
    source: str,
    required_headers: tuple[str, ...],
) -> list[dict[str, str]]:
    text = content.decode(CSV_ENCODING, errors="strict")
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";")
    headers = [str(header).strip() for header in reader.fieldnames or []]
    validate_headers(headers, required_headers, source)

    rows: list[dict[str, str]] = []
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise CvmFundsError(
                f"linha CSV malformada: arquivo={source} linha={line_number}"
            )
        rows.append({str(key).strip(): value for key, value in row.items()})
    if not rows:
        raise CvmFundsError(f"arquivo CSV sem registros: arquivo={source}")
    return rows


def read_zip_member(content: bytes, member: str) -> bytes:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            return archive.read(member)
    except (BadZipFile, KeyError) as exc:
        raise CvmFundsError(f"não foi possível ler membro ZIP: {member}") from exc


def metadata_fields(content: bytes) -> set[str]:
    text = content.decode(CSV_ENCODING, errors="strict")
    return {
        line.partition(":")[2].strip()
        for line in text.splitlines()
        if line.startswith("Campo:")
    }


def validate_metadata_dictionary(
    content: bytes,
    required_headers: tuple[str, ...],
    source: str,
) -> None:
    documented = metadata_fields(content)
    missing = [field for field in required_headers if field not in documented]
    if missing:
        raise CvmFundsError(
            "dicionário oficial incompleto; carga bloqueada: "
            f"arquivo={source} campos_ausentes={missing}"
        )


def _first(*values) -> str | None:
    for value in values:
        text = clean(value)
        if text:
            return text
    return None


def _base_record(
    *,
    source_file: str,
    regime: str,
    record_type: str,
    official_identifier: str | None,
    name: str | None,
    reference_date: date,
    raw: dict[str, str],
) -> dict:
    normalized_name = normalize_name(name)
    if not official_identifier:
        raise CvmFundsError(
            f"registro sem identificador oficial: arquivo={source_file}"
        )
    if not normalized_name:
        raise CvmFundsError(
            f"registro sem denominação social: arquivo={source_file} "
            f"identificador={official_identifier}"
        )
    return {
        "fonte_codigo": SOURCE_CODE,
        "fonte_arquivo": source_file,
        "regime_regulatorio": regime,
        "tipo_registro": record_type,
        "identificador_oficial": official_identifier,
        "id_registro_fundo": None,
        "id_registro_classe": None,
        "id_subclasse": None,
        "codigo_cvm": None,
        "cnpj_registro_normalizado": None,
        "cnpj_fundo_normalizado": None,
        "cnpj_classe_normalizado": None,
        "denominacao_social": clean(name),
        "denominacao_normalizada": normalized_name,
        "situacao": None,
        "tipo_fundo": None,
        "tipo_classe": None,
        "categoria_regulatoria": None,
        "classificacao_anbima": None,
        "data_registro": None,
        "data_constituicao": None,
        "data_inicio_atividade": None,
        "data_inicio_situacao": None,
        "data_cancelamento": None,
        "data_adaptacao_rcvm175": None,
        "administrador_cnpj_normalizado": None,
        "administrador_nome": None,
        "gestor_documento_normalizado": None,
        "gestor_nome": None,
        "data_referencia": reference_date,
        "dados_raw": raw,
    }


def transform_fund(
    row: dict[str, str], reference_date: date
) -> dict:
    fund_id = clean(row.get("ID_Registro_Fundo"))
    cnpj = normalize_cnpj(row.get("CNPJ_Fundo"))
    record = _base_record(
        source_file="registro_fundo.csv",
        regime="RCVM175",
        record_type="FUNDO",
        official_identifier=_first(fund_id, row.get("Codigo_CVM"), cnpj),
        name=row.get("Denominacao_Social"),
        reference_date=reference_date,
        raw=row,
    )
    record.update(
        {
            "id_registro_fundo": fund_id,
            "codigo_cvm": clean(row.get("Codigo_CVM")),
            "cnpj_registro_normalizado": cnpj,
            "cnpj_fundo_normalizado": cnpj,
            "situacao": clean(row.get("Situacao")),
            "tipo_fundo": clean(row.get("Tipo_Fundo")),
            "categoria_regulatoria": clean(row.get("Tipo_Fundo")),
            "data_registro": parse_date(
                row.get("Data_Registro"),
                field="Data_Registro",
                source="registro_fundo.csv",
            ),
            "data_constituicao": parse_date(
                row.get("Data_Constituicao"),
                field="Data_Constituicao",
                source="registro_fundo.csv",
            ),
            "data_inicio_situacao": parse_date(
                row.get("Data_Inicio_Situacao"),
                field="Data_Inicio_Situacao",
                source="registro_fundo.csv",
            ),
            "data_cancelamento": parse_date(
                row.get("Data_Cancelamento"),
                field="Data_Cancelamento",
                source="registro_fundo.csv",
            ),
            "data_adaptacao_rcvm175": parse_date(
                row.get("Data_Adaptacao_RCVM175"),
                field="Data_Adaptacao_RCVM175",
                source="registro_fundo.csv",
            ),
            "administrador_cnpj_normalizado": normalize_cnpj(
                row.get("CNPJ_Administrador")
            ),
            "administrador_nome": clean(row.get("Administrador")),
            "gestor_documento_normalizado": normalize_document(
                row.get("CPF_CNPJ_Gestor")
            ),
            "gestor_nome": clean(row.get("Gestor")),
        }
    )
    return record


def transform_class(
    row: dict[str, str], reference_date: date
) -> dict:
    class_id = clean(row.get("ID_Registro_Classe"))
    class_cnpj = normalize_cnpj(row.get("CNPJ_Classe"))
    record = _base_record(
        source_file="registro_classe.csv",
        regime="RCVM175",
        record_type="CLASSE",
        official_identifier=_first(
            class_id, row.get("Codigo_CVM"), class_cnpj
        ),
        name=row.get("Denominacao_Social"),
        reference_date=reference_date,
        raw=row,
    )
    record.update(
        {
            "id_registro_fundo": clean(row.get("ID_Registro_Fundo")),
            "id_registro_classe": class_id,
            "codigo_cvm": clean(row.get("Codigo_CVM")),
            "cnpj_registro_normalizado": class_cnpj,
            "cnpj_classe_normalizado": class_cnpj,
            "situacao": clean(row.get("Situacao")),
            "tipo_classe": clean(row.get("Tipo_Classe")),
            "categoria_regulatoria": _first(
                row.get("Classificacao"), row.get("Tipo_Classe")
            ),
            "classificacao_anbima": clean(
                row.get("Classificacao_Anbima")
            ),
            "data_registro": parse_date(
                row.get("Data_Registro"),
                field="Data_Registro",
                source="registro_classe.csv",
            ),
            "data_constituicao": parse_date(
                row.get("Data_Constituicao"),
                field="Data_Constituicao",
                source="registro_classe.csv",
            ),
            "data_inicio_atividade": parse_date(
                row.get("Data_Inicio"),
                field="Data_Inicio",
                source="registro_classe.csv",
            ),
            "data_inicio_situacao": parse_date(
                row.get("Data_Inicio_Situacao"),
                field="Data_Inicio_Situacao",
                source="registro_classe.csv",
            ),
        }
    )
    return record


def transform_subclass(
    row: dict[str, str], reference_date: date
) -> dict:
    subclass_id = clean(row.get("ID_Subclasse"))
    record = _base_record(
        source_file="registro_subclasse.csv",
        regime="RCVM175",
        record_type="SUBCLASSE",
        official_identifier=_first(subclass_id, row.get("Codigo_CVM")),
        name=row.get("Denominacao_Social"),
        reference_date=reference_date,
        raw=row,
    )
    record.update(
        {
            "id_registro_classe": clean(row.get("ID_Registro_Classe")),
            "id_subclasse": subclass_id,
            "codigo_cvm": clean(row.get("Codigo_CVM")),
            "situacao": clean(row.get("Situacao")),
            "data_constituicao": parse_date(
                row.get("Data_Constituicao"),
                field="Data_Constituicao",
                source="registro_subclasse.csv",
            ),
            "data_inicio_atividade": parse_date(
                row.get("Data_Inicio"),
                field="Data_Inicio",
                source="registro_subclasse.csv",
            ),
            "data_inicio_situacao": parse_date(
                row.get("Data_Inicio_Situacao"),
                field="Data_Inicio_Situacao",
                source="registro_subclasse.csv",
            ),
        }
    )
    return record


def transform_legacy(
    row: dict[str, str], reference_date: date
) -> dict:
    cnpj = normalize_cnpj(row.get("CNPJ_FUNDO"))
    code = clean(row.get("CD_CVM"))
    record = _base_record(
        source_file=LEGACY_DATA_FILE,
        regime="CAD_FI_LEGADO",
        record_type="FUNDO",
        official_identifier=_first(code, cnpj),
        name=row.get("DENOM_SOCIAL"),
        reference_date=reference_date,
        raw=row,
    )
    record.update(
        {
            "codigo_cvm": code,
            "cnpj_registro_normalizado": cnpj,
            "cnpj_fundo_normalizado": cnpj,
            "situacao": clean(row.get("SIT")),
            "tipo_fundo": clean(row.get("TP_FUNDO")),
            "categoria_regulatoria": clean(row.get("CLASSE")),
            "classificacao_anbima": clean(row.get("CLASSE_ANBIMA")),
            "data_registro": parse_date(
                row.get("DT_REG"),
                field="DT_REG",
                source=LEGACY_DATA_FILE,
            ),
            "data_constituicao": parse_date(
                row.get("DT_CONST"),
                field="DT_CONST",
                source=LEGACY_DATA_FILE,
            ),
            "data_inicio_atividade": parse_date(
                row.get("DT_INI_ATIV"),
                field="DT_INI_ATIV",
                source=LEGACY_DATA_FILE,
            ),
            "data_inicio_situacao": parse_date(
                row.get("DT_INI_SIT"),
                field="DT_INI_SIT",
                source=LEGACY_DATA_FILE,
            ),
            "data_cancelamento": parse_date(
                row.get("DT_CANCEL"),
                field="DT_CANCEL",
                source=LEGACY_DATA_FILE,
            ),
            "administrador_cnpj_normalizado": normalize_cnpj(
                row.get("CNPJ_ADMIN")
            ),
            "administrador_nome": clean(row.get("ADMIN")),
            "gestor_documento_normalizado": normalize_document(
                row.get("CPF_CNPJ_GESTOR")
            ),
            "gestor_nome": clean(row.get("GESTOR")),
        }
    )
    return record


def derive_reference_date(
    artifacts: dict[str, DownloadedArtifact],
) -> date:
    dates = [
        artifact.last_modified.date()
        for name, artifact in artifacts.items()
        if name in {DATA_ARCHIVE, LEGACY_DATA_FILE}
        and artifact.last_modified is not None
    ]
    return max(dates) if dates else datetime.now(timezone.utc).date()


def parse_official_artifacts(
    artifacts: dict[str, DownloadedArtifact],
) -> tuple[list[dict], date]:
    missing_artifacts = sorted(set(OFFICIAL_URLS).difference(artifacts))
    if missing_artifacts:
        raise CvmFundsError(
            f"artefatos oficiais ausentes: {missing_artifacts}"
        )

    reference_date = derive_reference_date(artifacts)
    data_zip = artifacts[DATA_ARCHIVE].content
    data_members = inspect_zip(data_zip, DATA_MEMBERS, DATA_ARCHIVE)
    metadata_zip = artifacts[METADATA_ARCHIVE].content
    metadata_members = inspect_zip(
        metadata_zip, METADATA_MEMBERS, METADATA_ARCHIVE
    )

    raw_rows: dict[str, list[dict[str, str]]] = {}
    for record_type, member in data_members.items():
        raw_rows[record_type] = parse_csv_content(
            read_zip_member(data_zip, member),
            source=member,
            required_headers=REQUIRED_HEADERS[record_type],
        )
        metadata_member = metadata_members[record_type]
        validate_metadata_dictionary(
            read_zip_member(metadata_zip, metadata_member),
            REQUIRED_HEADERS[record_type],
            metadata_member,
        )

    legacy_rows = parse_csv_content(
        artifacts[LEGACY_DATA_FILE].content,
        source=LEGACY_DATA_FILE,
        required_headers=LEGACY_HEADERS,
    )
    validate_metadata_dictionary(
        artifacts[LEGACY_METADATA_FILE].content,
        LEGACY_HEADERS,
        LEGACY_METADATA_FILE,
    )

    funds = [transform_fund(row, reference_date) for row in raw_rows["FUNDO"]]
    classes = [
        transform_class(row, reference_date) for row in raw_rows["CLASSE"]
    ]
    subclasses = [
        transform_subclass(row, reference_date)
        for row in raw_rows["SUBCLASSE"]
    ]
    legacy = [transform_legacy(row, reference_date) for row in legacy_rows]

    funds_by_id = {
        record["id_registro_fundo"]: record
        for record in funds
        if record["id_registro_fundo"]
    }
    classes_by_id = {
        record["id_registro_classe"]: record
        for record in classes
        if record["id_registro_classe"]
    }

    for record in classes:
        parent = funds_by_id.get(record["id_registro_fundo"])
        if parent:
            record["cnpj_fundo_normalizado"] = parent[
                "cnpj_fundo_normalizado"
            ]
            record["administrador_cnpj_normalizado"] = parent[
                "administrador_cnpj_normalizado"
            ]
            record["administrador_nome"] = parent["administrador_nome"]
            record["gestor_documento_normalizado"] = parent[
                "gestor_documento_normalizado"
            ]
            record["gestor_nome"] = parent["gestor_nome"]

    for record in subclasses:
        parent_class = classes_by_id.get(record["id_registro_classe"])
        if not parent_class:
            continue
        record["id_registro_fundo"] = parent_class["id_registro_fundo"]
        record["cnpj_classe_normalizado"] = parent_class[
            "cnpj_classe_normalizado"
        ]
        record["cnpj_fundo_normalizado"] = parent_class[
            "cnpj_fundo_normalizado"
        ]
        record["cnpj_registro_normalizado"] = _first(
            record["cnpj_classe_normalizado"],
            record["cnpj_fundo_normalizado"],
        )
        record["tipo_classe"] = parent_class["tipo_classe"]
        record["categoria_regulatoria"] = parent_class[
            "categoria_regulatoria"
        ]
        record["classificacao_anbima"] = parent_class[
            "classificacao_anbima"
        ]
        record["administrador_cnpj_normalizado"] = parent_class[
            "administrador_cnpj_normalizado"
        ]
        record["administrador_nome"] = parent_class["administrador_nome"]
        record["gestor_documento_normalizado"] = parent_class[
            "gestor_documento_normalizado"
        ]
        record["gestor_nome"] = parent_class["gestor_nome"]

    return [*funds, *classes, *subclasses, *legacy], reference_date


def is_active_status(value) -> bool:
    return normalize_name(value) == "EM FUNCIONAMENTO NORMAL"


def dataset_profile(records: list[dict]) -> dict:
    total = len(records)
    identifiers = sum(bool(row["identificador_oficial"]) for row in records)
    cnpjs = sum(bool(row["cnpj_registro_normalizado"]) for row in records)
    types = Counter(row["tipo_registro"] for row in records)
    sources = Counter(row["fonte_arquivo"] for row in records)
    active = sum(is_active_status(row["situacao"]) for row in records)
    return {
        "total_registros": total,
        "fundos": types["FUNDO"],
        "classes": types["CLASSE"],
        "subclasses": types["SUBCLASSE"],
        "ativos": active,
        "inativos_ou_pendentes": total - active,
        "cobertura_identificador": identifiers / total if total else 0.0,
        "cobertura_cnpj": cnpjs / total if total else 0.0,
        "por_arquivo": dict(sorted(sources.items())),
    }


def validate_dataset_sanity(
    records: list[dict], previous_profile: dict | None = None
) -> dict:
    profile = dataset_profile(records)
    checks = (
        (profile["total_registros"] >= MIN_TOTAL_RECORDS, "total_registros"),
        (profile["fundos"] >= MIN_FUND_RECORDS, "fundos"),
        (profile["classes"] > 0, "classes"),
        (profile["subclasses"] > 0, "subclasses"),
        (profile["ativos"] > 0, "registros_ativos"),
        (
            profile["cobertura_identificador"] >= MIN_IDENTIFIER_COVERAGE,
            "cobertura_identificador",
        ),
        (profile["cobertura_cnpj"] >= MIN_CNPJ_COVERAGE, "cobertura_cnpj"),
    )
    for valid, rule in checks:
        if not valid:
            raise CvmFundsError(
                f"sanidade[{rule}] reprovada: perfil_atual="
                f"{json.dumps(profile, ensure_ascii=False, sort_keys=True)}"
            )

    variation = None
    previous_total = int((previous_profile or {}).get("total_registros") or 0)
    if previous_total:
        variation = abs(profile["total_registros"] - previous_total) / previous_total
        if variation > MAX_TOTAL_VARIATION_RATIO:
            raise CvmFundsError(
                "sanidade[variacao_total] reprovada: "
                f"anterior={previous_total} atual={profile['total_registros']} "
                f"variacao={variation:.2%} "
                f"limite={MAX_TOTAL_VARIATION_RATIO:.2%}"
            )

    return {
        "valido": True,
        "perfil": profile,
        "total_anterior": previous_total or None,
        "variacao_total": variation,
    }


def load_previous_profile(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from investimento.cvm_fundos_cadastro"
        )
        total = cur.fetchone()[0]
    return {"total_registros": total} if total else None


def cvm_copy_row(record: dict) -> tuple:
    return tuple(
        json.dumps(record[column], ensure_ascii=False)
        if column == "dados_raw"
        else record[column]
        for column in CVM_COPY_COLUMNS
    )


def copy_rows(cur, table: str, columns: tuple[str, ...], rows) -> None:
    column_list = ", ".join(columns)
    with cur.copy(f"copy {table} ({column_list}) from stdin") as copy:
        for row in rows:
            copy.write_row(row)


def replace_current_snapshot(conn, records: list[dict]) -> int:
    progress(f"carregando staging CVM via COPY: linhas={len(records)}")
    with conn.cursor() as cur:
        cur.execute(
            """
            create temporary table tmp_cvm_fundos_cadastro
            (like investimento.cvm_fundos_cadastro including defaults)
            on commit drop
            """
        )
        copy_rows(
            cur,
            "tmp_cvm_fundos_cadastro",
            CVM_COPY_COLUMNS,
            (cvm_copy_row(record) for record in records),
        )
        cur.execute(
            "create index on tmp_cvm_fundos_cadastro "
            "(denominacao_normalizada)"
        )
        cur.execute("analyze tmp_cvm_fundos_cadastro")
        cur.execute("delete from investimento.cvm_fundos_cadastro")
        columns = ", ".join(CVM_COPY_COLUMNS)
        cur.execute(
            f"""
            insert into investimento.cvm_fundos_cadastro ({columns})
            select {columns}
            from tmp_cvm_fundos_cadastro
            """
        )
        return cur.rowcount


def load_b3_funds(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(B3_FUNDS_SQL)
        columns = [description.name for description in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _candidate_key(candidate: dict) -> tuple:
    cnpj = candidate.get("cnpj_registro_normalizado")
    if cnpj:
        return ("CNPJ", cnpj)
    return (
        candidate.get("tipo_registro"),
        candidate.get("identificador_oficial"),
        candidate.get("fonte_arquivo"),
    )


def _candidate_priority(candidate: dict) -> tuple:
    return (
        candidate.get("regime_regulatorio") == "RCVM175",
        candidate.get("tipo_registro") == "CLASSE",
        is_active_status(candidate.get("situacao")),
    )


def _collapse_candidates(candidates: list[dict]) -> list[dict]:
    by_key: dict[tuple, dict] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        current = by_key.get(key)
        if current is None or _candidate_priority(candidate) > _candidate_priority(
            current
        ):
            by_key[key] = candidate
    return list(by_key.values())


def _match_result(
    b3_fund: dict,
    *,
    method: str,
    candidate: dict | None,
    confidence: float | None,
    ambiguous_count: int = 0,
) -> dict:
    return {
        "ticker": b3_fund.get("ticker"),
        "nome_b3": _first(b3_fund.get("nome"), b3_fund.get("nome_pregao")),
        "metodo": method,
        "confianca": confidence,
        "candidatos_ambiguos": ambiguous_count,
        "cvm_nome": candidate.get("denominacao_social") if candidate else None,
        "cvm_cnpj": (
            candidate.get("cnpj_registro_normalizado") if candidate else None
        ),
        "cvm_codigo": candidate.get("codigo_cvm") if candidate else None,
        "cvm_situacao": candidate.get("situacao") if candidate else None,
        "cvm_tipo_registro": (
            candidate.get("tipo_registro") if candidate else None
        ),
        "cvm_categoria": (
            candidate.get("categoria_regulatoria") if candidate else None
        ),
        "seguro_para_validacao_futura": bool(
            candidate and method == "A_IDENTIFICADOR_OFICIAL_EXATO"
        ),
        "validacao_automatica": False,
        "altera_cadastro_mestre": False,
    }


class FundMatcher:
    def __init__(self, records: list[dict]):
        candidates = [
            record
            for record in records
            if record["tipo_registro"] in {"FUNDO", "CLASSE"}
        ]
        self.by_cnpj: dict[str, list[dict]] = defaultdict(list)
        self.by_code: dict[str, list[dict]] = defaultdict(list)
        self.by_name: dict[str, list[dict]] = defaultdict(list)
        self.by_token: dict[str, list[dict]] = defaultdict(list)
        for candidate in candidates:
            cnpj = candidate.get("cnpj_registro_normalizado")
            code = clean(candidate.get("codigo_cvm"))
            name = candidate["denominacao_normalizada"]
            if cnpj:
                self.by_cnpj[cnpj].append(candidate)
            if code:
                self.by_code[code].append(candidate)
            self.by_name[name].append(candidate)
            for token in set(name.split()).difference(GENERIC_MATCH_TOKENS):
                self.by_token[token].append(candidate)

    def match(self, b3_fund: dict) -> dict:
        b3_cnpj = normalize_cnpj(b3_fund.get("cnpj"))
        b3_code = clean(b3_fund.get("codigo_cvm"))
        identifier_candidates: list[dict] = []
        if b3_cnpj:
            identifier_candidates.extend(self.by_cnpj.get(b3_cnpj, []))
        if b3_code:
            identifier_candidates.extend(self.by_code.get(b3_code, []))
        collapsed = _collapse_candidates(identifier_candidates)
        if len(collapsed) == 1:
            return _match_result(
                b3_fund,
                method="A_IDENTIFICADOR_OFICIAL_EXATO",
                candidate=collapsed[0],
                confidence=1.0,
            )
        if len(collapsed) > 1:
            return _match_result(
                b3_fund,
                method="D_AMBIGUO_IDENTIFICADOR",
                candidate=None,
                confidence=None,
                ambiguous_count=len(collapsed),
            )

        normalized_names = {
            normalize_name(b3_fund.get("nome")),
            normalize_name(b3_fund.get("nome_pregao")),
        }
        normalized_names.discard("")
        exact_candidates: list[dict] = []
        for name in normalized_names:
            exact_candidates.extend(self.by_name.get(name, []))
        collapsed = _collapse_candidates(exact_candidates)
        if len(collapsed) == 1:
            return _match_result(
                b3_fund,
                method="B_NOME_EXATO_UNICO",
                candidate=collapsed[0],
                confidence=1.0,
            )
        if len(collapsed) > 1:
            return _match_result(
                b3_fund,
                method="D_AMBIGUO_NOME_EXATO",
                candidate=None,
                confidence=None,
                ambiguous_count=len(collapsed),
            )

        query_name = max(normalized_names, key=len, default="")
        significant_tokens = set(query_name.split()).difference(
            GENERIC_MATCH_TOKENS
        )
        token_buckets = [
            self.by_token[token]
            for token in significant_tokens
            if self.by_token.get(token)
        ]
        if not token_buckets:
            return _match_result(
                b3_fund,
                method="D_NAO_ENCONTRADO",
                candidate=None,
                confidence=None,
            )

        fuzzy_candidates = _collapse_candidates(min(token_buckets, key=len))
        scored = sorted(
            [
                (
                    SequenceMatcher(
                        None,
                        query_name,
                        candidate["denominacao_normalizada"],
                    ).ratio(),
                    candidate,
                )
                for candidate in fuzzy_candidates
            ],
            key=lambda item: item[0],
        )
        best_score, best_candidate = scored[-1]
        if best_score < 0.72:
            return _match_result(
                b3_fund,
                method="D_NAO_ENCONTRADO",
                candidate=best_candidate,
                confidence=best_score,
            )
        if len(scored) > 1 and best_score - scored[-2][0] < 0.02:
            return _match_result(
                b3_fund,
                method="D_AMBIGUO_APROXIMADO",
                candidate=None,
                confidence=best_score,
                ambiguous_count=2,
            )
        return _match_result(
            b3_fund,
            method="C_NOME_APROXIMADO",
            candidate=best_candidate,
            confidence=best_score,
        )


def build_audit(records: list[dict], b3_funds: list[dict]) -> dict:
    profile = dataset_profile(records)
    matcher = FundMatcher(records)
    matches = [matcher.match(fund) for fund in b3_funds]
    methods = Counter(match["metodo"] for match in matches)
    method_groups = {
        "A_identificador_exato": sum(
            count for method, count in methods.items() if method.startswith("A_")
        ),
        "B_nome_exato_unico": sum(
            count for method, count in methods.items() if method.startswith("B_")
        ),
        "C_nome_aproximado": sum(
            count for method, count in methods.items() if method.startswith("C_")
        ),
        "D_ambiguo": sum(
            count
            for method, count in methods.items()
            if method.startswith("D_AMBIGUO")
        ),
        "D_nao_encontrado": methods["D_NAO_ENCONTRADO"],
    }
    category_distribution = Counter(
        _first(
            record.get("categoria_regulatoria"),
            record.get("tipo_fundo"),
            record.get("tipo_classe"),
            "NAO_INFORMADA",
        )
        for record in records
    )
    matches_by_ticker = {match["ticker"]: match for match in matches}
    return {
        "cvm": {
            **profile,
            "distribuicao_categoria": dict(
                sorted(category_distribution.items())
            ),
        },
        "b3_fundos_analisados": len(b3_funds),
        "correspondencias": method_groups,
        "metodos_detalhados": dict(sorted(methods.items())),
        "exemplos": {
            ticker: matches_by_ticker.get(ticker) for ticker in AUDIT_TICKERS
        },
        "alteracoes_em_ativos": 0,
    }


def process_collection(
    conn, artifacts: dict[str, DownloadedArtifact]
) -> tuple[dict, dict, int, date]:
    progress("validando ZIPs, dicionários e cabeçalhos oficiais")
    records, reference_date = parse_official_artifacts(artifacts)
    previous_profile = load_previous_profile(conn)
    sanity = validate_dataset_sanity(records, previous_profile)
    progress(
        "sanidade aprovada antes de substituir o snapshot: "
        f"referencia={reference_date} "
        f"perfil={json.dumps(sanity['perfil'], ensure_ascii=False, sort_keys=True)}"
    )

    written = replace_current_snapshot(conn, records)
    b3_funds = load_b3_funds(conn)
    audit = build_audit(records, b3_funds)
    conn.commit()
    return sanity, audit, written, reference_date


def log_start(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into investimento.coletas_log (
                fonte_codigo, processo, status
            )
            values (%s, %s, 'INICIADO')
            returning id
            """,
            (SOURCE_CODE, PROCESS_NAME),
        )
        log_id = cur.fetchone()[0]
    conn.commit()
    return log_id


def log_end(
    conn,
    log_id: int,
    status: str,
    *,
    read: int | None,
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
            (status, read, written, message[:1500], log_id),
        )
    conn.commit()


def main() -> int:
    progress("iniciando coleta do cadastro oficial de fundos da CVM")
    conn = connect()
    log_id = log_start(conn)
    reference_date: date | None = None
    try:
        artifacts = download_official_artifacts()
        reference_date = derive_reference_date(artifacts)
        sanity, audit, written, reference_date = process_collection(
            conn, artifacts
        )
        result = {
            "data_referencia": str(reference_date),
            "sanidade": sanity,
            "registros_lidos": sanity["perfil"]["total_registros"],
            "registros_gravados": written,
            "auditoria": audit,
        }
        log_message = json.dumps(
            {
                "data_referencia": str(reference_date),
                "sanidade": sanity,
                "registros_lidos": result["registros_lidos"],
                "registros_gravados": written,
                "b3_fundos_analisados": audit["b3_fundos_analisados"],
                "correspondencias": audit["correspondencias"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        log_end(
            conn,
            log_id,
            "SUCESSO",
            read=result["registros_lidos"],
            written=written,
            message=log_message,
        )
        progress(
            "AUDITORIA | "
            + json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        progress("SUCESSO")
        return 0
    except Exception as exc:
        conn.rollback()
        error_message = json.dumps(
            {
                "data_referencia": (
                    str(reference_date) if reference_date else None
                ),
                "erro": str(exc),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            log_end(
                conn,
                log_id,
                "ERRO",
                read=None,
                written=0,
                message=error_message,
            )
        finally:
            print(
                f"CVM fundos: ERROR | {error_message}",
                file=sys.stderr,
                flush=True,
            )
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
