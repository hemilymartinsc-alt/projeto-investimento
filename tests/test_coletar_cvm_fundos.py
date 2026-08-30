from __future__ import annotations

import io
import re
import sys
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from zipfile import ZipFile


def _ensure_dependency_stub(name, **attributes):
    try:
        module = __import__(name)
    except ModuleNotFoundError:
        module = types.ModuleType(name)
        sys.modules[name] = module
    for attribute, value in attributes.items():
        if not hasattr(module, attribute):
            setattr(module, attribute, value)


_ensure_dependency_stub(
    "requests", RequestException=RuntimeError, get=lambda *args, **kwargs: None
)
_ensure_dependency_stub("psycopg", connect=lambda *args, **kwargs: None)
_ensure_dependency_stub("dotenv", load_dotenv=lambda *args, **kwargs: None)

from src.jobs import coletar_cvm_fundos as job  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "012_criar_cadastro_cvm_fundos.sql"
WORKFLOW = ROOT / ".github" / "workflows" / "cvm-fundos.yml"
REFERENCE_DATE = date(2026, 8, 29)


def csv_bytes(headers, values=None):
    values = values or {}
    row = [values.get(header, "") for header in headers]
    content = ";".join(headers) + "\n" + ";".join(row) + "\n"
    return content.encode(job.CSV_ENCODING)


def zip_bytes(members):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def metadata_bytes(headers):
    return "\n".join(f"Campo: {header}" for header in headers).encode(
        job.CSV_ENCODING
    )


def artifact(name, content):
    return job.DownloadedArtifact(
        name=name,
        url=job.OFFICIAL_URLS[name],
        content=content,
        last_modified=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )


def official_artifacts():
    fund = csv_bytes(
        job.FUND_HEADERS,
        {
            "ID_Registro_Fundo": "10",
            "CNPJ_Fundo": "12.345.678/0001-90",
            "Codigo_CVM": "100",
            "Data_Registro": "2025-01-01",
            "Data_Constituicao": "2024-12-01",
            "Tipo_Fundo": "FIF",
            "Denominacao_Social": "FUNDO ALFA",
            "Situacao": "Em Funcionamento Normal",
            "Data_Inicio_Situacao": "2025-01-01",
            "CNPJ_Administrador": "98.765.432/0001-10",
            "Administrador": "ADMIN ALFA",
            "CPF_CNPJ_Gestor": "123.456.789-01",
            "Gestor": "GESTOR ALFA",
        },
    )
    class_row = csv_bytes(
        job.CLASS_HEADERS,
        {
            "ID_Registro_Fundo": "10",
            "ID_Registro_Classe": "20",
            "CNPJ_Classe": "11.111.111/0001-11",
            "Codigo_CVM": "200",
            "Data_Registro": "2025-01-02",
            "Data_Constituicao": "2025-01-01",
            "Data_Inicio": "2025-01-03",
            "Tipo_Classe": "Classes de Cotas de Fundos FII",
            "Denominacao_Social": "CLASSE ALFA FII",
            "Situacao": "Em Funcionamento Normal",
            "Data_Inicio_Situacao": "2025-01-03",
            "Classificacao": "Fundo Imobiliário",
        },
    )
    subclass = csv_bytes(
        job.SUBCLASS_HEADERS,
        {
            "ID_Registro_Classe": "20",
            "ID_Subclasse": "SUB20A",
            "Codigo_CVM": "201",
            "Data_Constituicao": "2025-01-01",
            "Data_Inicio": "2025-01-03",
            "Denominacao_Social": "SUBCLASSE A",
            "Situacao": "Em Funcionamento Normal",
            "Data_Inicio_Situacao": "2025-01-03",
        },
    )
    legacy = csv_bytes(
        job.LEGACY_HEADERS,
        {
            "TP_FUNDO": "FII",
            "CNPJ_FUNDO": "22.222.222/0001-22",
            "DENOM_SOCIAL": "FUNDO LEGADO",
            "DT_REG": "2000-01-01",
            "DT_CONST": "1999-01-01",
            "CD_CVM": "300",
            "SIT": "CANCELADA",
        },
    )
    data_zip = zip_bytes(
        {
            "dados/registro_fundo.csv": fund,
            "dados/registro_classe.csv": class_row,
            "dados/registro_subclasse.csv": subclass,
        }
    )
    metadata_zip = zip_bytes(
        {
            "meta_registro_fundo.txt": metadata_bytes(job.FUND_HEADERS),
            "meta_registro_classe.txt": metadata_bytes(job.CLASS_HEADERS),
            "meta_registro_subclasse.txt": metadata_bytes(
                job.SUBCLASS_HEADERS
            ),
        }
    )
    return {
        job.DATA_ARCHIVE: artifact(job.DATA_ARCHIVE, data_zip),
        job.LEGACY_DATA_FILE: artifact(job.LEGACY_DATA_FILE, legacy),
        job.METADATA_ARCHIVE: artifact(
            job.METADATA_ARCHIVE, metadata_zip
        ),
        job.LEGACY_METADATA_FILE: artifact(
            job.LEGACY_METADATA_FILE,
            metadata_bytes(job.LEGACY_HEADERS),
        ),
    }


def cvm_candidate(name, cnpj, *, code="", situation="Em Funcionamento Normal"):
    return {
        "tipo_registro": "CLASSE",
        "regime_regulatorio": "RCVM175",
        "identificador_oficial": cnpj,
        "cnpj_registro_normalizado": job.normalize_cnpj(cnpj),
        "codigo_cvm": code,
        "denominacao_social": name,
        "denominacao_normalizada": job.normalize_name(name),
        "situacao": situation,
        "categoria_regulatoria": "Fundo Imobiliário",
        "fonte_arquivo": "registro_classe.csv",
    }


def b3_fund(name, *, ticker="TEST11", cnpj=None, code=None):
    return {
        "ticker": ticker,
        "nome": name,
        "nome_pregao": ticker,
        "cnpj": cnpj,
        "codigo_cvm": code,
    }


def sanity_record(record_type, number, *, active=True):
    return {
        "tipo_registro": record_type,
        "identificador_oficial": str(number),
        "cnpj_registro_normalizado": f"{number:014d}",
        "situacao": (
            "Em Funcionamento Normal" if active else "Cancelado"
        ),
        "fonte_arquivo": f"registro_{record_type.lower()}.csv",
    }


class OfficialLayoutTest(unittest.TestCase):
    def test_zip_integrity_member_detection_and_hierarchy(self):
        artifacts = official_artifacts()
        members = job.inspect_zip(
            artifacts[job.DATA_ARCHIVE].content,
            job.DATA_MEMBERS,
            job.DATA_ARCHIVE,
        )

        self.assertEqual(
            members,
            {
                "FUNDO": "dados/registro_fundo.csv",
                "CLASSE": "dados/registro_classe.csv",
                "SUBCLASSE": "dados/registro_subclasse.csv",
            },
        )

        records, reference_date = job.parse_official_artifacts(artifacts)

        self.assertEqual(reference_date, REFERENCE_DATE)
        self.assertEqual(len(records), 4)
        subclass = next(
            row for row in records if row["tipo_registro"] == "SUBCLASSE"
        )
        self.assertEqual(subclass["id_registro_fundo"], "10")
        self.assertEqual(subclass["cnpj_fundo_normalizado"], "12345678000190")
        self.assertEqual(subclass["cnpj_classe_normalizado"], "11111111000111")
        self.assertEqual(subclass["administrador_nome"], "ADMIN ALFA")

    def test_required_header_change_fails_closed(self):
        broken_headers = tuple(
            header for header in job.FUND_HEADERS if header != "CNPJ_Fundo"
        )
        content = csv_bytes(broken_headers)

        with self.assertRaisesRegex(
            job.CvmFundsError, "colunas_obrigatorias_ausentes"
        ):
            job.parse_csv_content(
                content,
                source="registro_fundo.csv",
                required_headers=job.FUND_HEADERS,
            )

    def test_cnpj_and_name_normalization(self):
        self.assertEqual(
            job.normalize_cnpj("12.345.678/0001-90"), "12345678000190"
        )
        self.assertIsNone(job.normalize_cnpj("123"))
        self.assertEqual(
            job.normalize_name(
                "Áquila FII - Fundo de Investimento Imobiliário"
            ),
            "AQUILA FII FUNDO DE INVESTIMENTO IMOBILIARIO",
        )
        self.assertEqual(
            job.normalize_name("ALFA FDO INV IMOB RESP LIM"),
            "ALFA FUNDO INVESTIMENTO IMOBILIARIO RESPONSABILIDADE LIMITADA",
        )


class MatchingAuditTest(unittest.TestCase):
    def test_official_identifier_exact_match(self):
        candidate = cvm_candidate("FUNDO ALFA", "12.345.678/0001-90")
        result = job.FundMatcher([candidate]).match(
            b3_fund(
                "NOME B3 DIFERENTE",
                cnpj="12.345.678/0001-90",
            )
        )

        self.assertEqual(result["metodo"], "A_IDENTIFICADOR_OFICIAL_EXATO")
        self.assertTrue(result["seguro_para_validacao_futura"])
        self.assertFalse(result["validacao_automatica"])
        self.assertFalse(result["altera_cadastro_mestre"])

    def test_exact_unique_name_is_high_confidence_candidate_only(self):
        candidate = cvm_candidate("FUNDO ALFA FII", "11.111.111/0001-11")
        result = job.FundMatcher([candidate]).match(
            b3_fund("Fundo Alfa FII")
        )

        self.assertEqual(result["metodo"], "B_NOME_EXATO_UNICO")
        self.assertEqual(result["confianca"], 1.0)
        self.assertFalse(result["seguro_para_validacao_futura"])
        self.assertFalse(result["validacao_automatica"])

    def test_duplicate_exact_name_is_ambiguous(self):
        candidates = [
            cvm_candidate("FUNDO ALFA FII", "11.111.111/0001-11"),
            cvm_candidate("FUNDO ALFA FII", "22.222.222/0001-22"),
        ]
        result = job.FundMatcher(candidates).match(
            b3_fund("FUNDO ALFA FII")
        )

        self.assertEqual(result["metodo"], "D_AMBIGUO_NOME_EXATO")
        self.assertEqual(result["candidatos_ambiguos"], 2)

    def test_fuzzy_match_never_validates_or_mutates_master(self):
        candidate = cvm_candidate(
            "ALFA FUNDO DE INVESTIMENTO IMOBILIARIO RL",
            "11.111.111/0001-11",
        )
        result = job.FundMatcher([candidate]).match(
            b3_fund("ALFA FUNDO DE INVESTIMENTO IMOBILIARIO")
        )

        self.assertEqual(result["metodo"], "C_NOME_APROXIMADO")
        self.assertFalse(result["seguro_para_validacao_futura"])
        self.assertFalse(result["validacao_automatica"])
        self.assertFalse(result["altera_cadastro_mestre"])

    def test_equal_fuzzy_scores_are_reported_as_ambiguous(self):
        candidates = [
            cvm_candidate("ALFA FII CLASSE A", "11.111.111/0001-11"),
            cvm_candidate("ALFA FII CLASSE B", "22.222.222/0001-22"),
        ]
        result = job.FundMatcher(candidates).match(
            b3_fund("ALFA FII CLASSE C")
        )

        self.assertEqual(result["metodo"], "D_AMBIGUO_APROXIMADO")
        self.assertFalse(result["validacao_automatica"])


class SanityAndPersistenceTest(unittest.TestCase):
    def test_sanity_failure_happens_before_snapshot_replacement(self):
        fake_conn = mock.Mock()
        artifacts = official_artifacts()
        records = [sanity_record("FUNDO", 1)]

        with (
            mock.patch.object(
                job,
                "parse_official_artifacts",
                return_value=(records, REFERENCE_DATE),
            ),
            mock.patch.object(job, "load_previous_profile", return_value=None),
            mock.patch.object(
                job,
                "validate_dataset_sanity",
                side_effect=job.CvmFundsError("sanidade reprovada"),
            ),
            mock.patch.object(job, "replace_current_snapshot") as replace,
            mock.patch.object(job, "load_b3_funds") as load_b3,
        ):
            with self.assertRaisesRegex(job.CvmFundsError, "sanidade"):
                job.process_collection(fake_conn, artifacts)

        replace.assert_not_called()
        load_b3.assert_not_called()
        fake_conn.commit.assert_not_called()

    def test_sanity_accepts_first_snapshot_and_rejects_abnormal_variation(self):
        records = [
            sanity_record("FUNDO", 1),
            sanity_record("CLASSE", 2),
            sanity_record("SUBCLASSE", 3),
        ]
        with (
            mock.patch.object(job, "MIN_TOTAL_RECORDS", 1),
            mock.patch.object(job, "MIN_FUND_RECORDS", 1),
        ):
            result = job.validate_dataset_sanity(records, None)
            self.assertTrue(result["valido"])
            self.assertIsNone(result["variacao_total"])

            with self.assertRaisesRegex(
                job.CvmFundsError, r"sanidade\[variacao_total\]"
            ):
                job.validate_dataset_sanity(
                    records, {"total_registros": 10}
                )

    def test_bulk_copy_replaces_only_cvm_snapshot(self):
        source = Path(job.__file__).read_text(encoding="utf-8").lower()

        self.assertIn("with cur.copy(", source)
        self.assertIn("tmp_cvm_fundos_cadastro", source)
        self.assertNotIn("executemany(", source)
        self.assertIn("delete from investimento.cvm_fundos_cadastro", source)

    def test_job_has_no_master_mutation_route(self):
        source = Path(job.__file__).read_text(encoding="utf-8").lower()
        master_mutation = re.compile(
            r"(?:insert\s+into|update|delete\s+from)\s+investimento\.ativos"
        )

        self.assertIsNone(master_mutation.search(source))
        self.assertIn("select *\nfrom investimento.ativos", source)
        self.assertIn("where classe = 'fundo'", source)

    def test_migration_is_audit_only_and_indexes_official_keys(self):
        migration = MIGRATION.read_text(encoding="utf-8").lower()

        for column in (
            "identificador_oficial",
            "tipo_registro",
            "cnpj_fundo_normalizado",
            "cnpj_classe_normalizado",
            "codigo_cvm",
            "denominacao_normalizada",
            "dados_raw",
        ):
            self.assertIn(column, migration)
        self.assertIn("'cvm_fundos_cad'", migration)
        self.assertIn("'oficial'", migration)
        self.assertIsNone(
            re.search(
                r"(?:insert\s+into|update|delete\s+from)\s+"
                r"investimento\.ativos",
                migration,
            )
        )

    def test_new_files_do_not_embed_credentials(self):
        source = Path(job.__file__).read_text(encoding="utf-8")
        migration = MIGRATION.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("DATABASE_URL", source)
        self.assertNotIn("DATABASE_URL", migration)
        self.assertNotRegex(source + migration + workflow, r"postgres(?:ql)?://")
        self.assertIn("DATABASE_URL: ${{ secrets.DATABASE_URL }}", workflow)


if __name__ == "__main__":
    unittest.main()
