from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


def _stub_optional_dependency(name, **attributes):
    try:
        __import__(name)
    except ModuleNotFoundError:
        module = types.ModuleType(name)
        for attribute, value in attributes.items():
            setattr(module, attribute, value)
        sys.modules[name] = module


_stub_optional_dependency("requests")
_stub_optional_dependency("psycopg")
_stub_optional_dependency("dotenv", load_dotenv=lambda *args, **kwargs: None)

from src.jobs import validate_b3_instrumentos as job  # noqa: E402
from src.jobs.validate_b3_instrumentos import (  # noqa: E402
    SnapshotSanityError,
    UPDATE_ABSENT_SQL,
    UPDATE_CANONICAL_SQL,
    annotate_universe,
    activity_state,
    build_audit,
    canonical_decision,
    normalize,
    parse_csv,
    preliminary_classification,
    process_snapshot,
    validate_snapshot_sanity,
)


REFERENCE_DATE = date(2026, 2, 11)
MIGRATION_009 = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "009_controle_atividade_b3.sql"
)


def instrument(
    ticker,
    category,
    *,
    segment="CASH",
    market="EQUITY-CASH",
    isin="BRTESTACNOR1",
    corporate_name="TESTE S.A.",
):
    return {
        "data_referencia": REFERENCE_DATE,
        "ticker": ticker,
        "isin": isin,
        "ativo_base": ticker,
        "descricao_ativo": ticker,
        "segmento_b3": segment,
        "mercado_b3": market,
        "categoria_b3": category,
        "descricao_b3": None,
        "cfi_code": None,
        "moeda": "BRL",
        "nome_corporativo": corporate_name,
        "nivel_governanca": None,
        "data_inicio_negociacao": REFERENCE_DATE,
        "data_fim_negociacao": date(9999, 12, 31),
        "data_expiracao": None,
        "status_arquivo": "Final",
        "raw_json": {},
    }


def sanity_sample(class_counts, noncanonical_count=0):
    category_by_class = {
        "ACAO": "SHARES",
        "BDR": "BDR",
        "ETF": "ETF EQUITIES",
        "FUNDO": "FUNDS",
    }
    instruments = []
    sequence = 0

    for classe, total in class_counts.items():
        for class_index in range(total):
            category = category_by_class[classe]
            if classe == "ACAO" and class_index == 0:
                category = "UNIT"
            instruments.append(
                instrument(
                    f"T{sequence:05d}",
                    category,
                    isin=f"BR{sequence:010d}",
                )
            )
            sequence += 1

    for _ in range(noncanonical_count):
        instruments.append(
            instrument(
                f"N{sequence:05d}",
                "SHARES",
                segment="EQUITY BLOCK TRADING LOT",
                isin=f"BR{sequence:010d}",
            )
        )
        sequence += 1

    annotate_universe(instruments, REFERENCE_DATE)
    return instruments


class CanonicalRulesTest(unittest.TestCase):
    def test_current_canonical_has_confirmed_b3_activity(self):
        current = instrument("PETR4", "SHARES", isin="BRPETRACNPR6")

        annotate_universe([current], REFERENCE_DATE)

        self.assertTrue(current["instrumento_canonico"])
        self.assertTrue(current["atividade_confirmada_b3"])
        self.assertEqual(current["status_atividade_b3"], "CONFIRMADA")
        self.assertIsNone(current["motivo_atividade_b3"])

    def test_preliminary_classification_uses_official_category(self):
        self.assertEqual(
            preliminary_classification(instrument("PETR4", "SHARES")),
            ("ACAO", None),
        )
        self.assertEqual(
            preliminary_classification(instrument("SANB11", "UNIT")),
            ("ACAO", "UNIT"),
        )
        self.assertEqual(
            preliminary_classification(
                instrument(
                    "HGLG11",
                    "FUNDS",
                    corporate_name="PATRIA LOG FUNDO INV IMOBILIARIO",
                )
            ),
            ("FUNDO", None),
        )
        self.assertEqual(
            preliminary_classification(
                instrument(
                    "AADA39",
                    "BDR",
                    corporate_name="21SHARES CARDANO ETP",
                )
            ),
            ("BDR", "ETP"),
        )

    def test_canonical_market_and_operational_exclusions(self):
        canonical = instrument("AFHI11", "FUNDS", isin="BRAFHICTF005")
        block = instrument(
            "AFHI11M",
            "FUNDS",
            segment="Equity Block Trading Lot",
            isin="BRAFHICTF005",
        )
        primary = instrument(
            "BOVAETF11H",
            "ETF PRIMARY MARKET GROSS SETTLEMENT",
            segment="ETF PRIMARY MARKET",
            market="FIXED INCOME",
            isin="BRBOVACTF003",
        )
        rights = instrument("TEST1", "RIGHTS")
        derivative = instrument(
            "PETRA100",
            "OPTION ON EQUITIES",
            segment="EQUITY CALL",
            market="EQUITY-DERIVATE",
        )

        self.assertEqual(canonical_decision(canonical, REFERENCE_DATE), (True, None))
        self.assertEqual(
            canonical_decision(block, REFERENCE_DATE),
            (False, "EQUITY_BLOCK_TRADING_LOT"),
        )
        self.assertEqual(
            canonical_decision(primary, REFERENCE_DATE),
            (False, "ETF_PRIMARY_MARKET"),
        )
        self.assertEqual(
            preliminary_classification(primary),
            ("ETF", "RENDA_VARIAVEL"),
        )
        self.assertEqual(
            canonical_decision(rights, REFERENCE_DATE),
            (False, "RIGHTS"),
        )
        self.assertEqual(
            canonical_decision(derivative, REFERENCE_DATE),
            (False, "DERIVATIVO"),
        )

    def test_fixed_income_etfs_require_official_primary_market_pair(self):
        normal_tickers = ("LFTI11", "LFTS11", "IMAB11", "B5P211")
        instruments = []

        for index, ticker in enumerate(normal_tickers):
            isin = f"BRFETF{index:06d}"
            asset = ticker.removesuffix("11")
            instruments.extend(
                [
                    instrument(
                        ticker,
                        "FIXED INCOME TRADABLE INSTRUMENT T1",
                        segment="FORWARD",
                        market="FIXED INCOME",
                        isin=isin,
                    ),
                    instrument(
                        f"{asset}ETF11H",
                        "ETF PRIMARY MARKET GROSS SETTLEMENT",
                        segment="ETF PRIMARY MARKET",
                        market="FIXED INCOME",
                        isin=isin,
                    ),
                ]
            )
            instruments[-2]["ativo_base"] = asset
            instruments[-1]["ativo_base"] = asset

        annotate_universe(instruments, REFERENCE_DATE)

        for ticker in normal_tickers:
            normal = next(item for item in instruments if item["ticker"] == ticker)
            self.assertTrue(normal["instrumento_canonico"], ticker)
            self.assertEqual(normal["classe_preliminar"], "ETF", ticker)
            self.assertEqual(normal["subclasse_preliminar"], "RENDA_FIXA", ticker)

        primary = next(
            item for item in instruments if item["ticker"].endswith("ETF11H")
        )
        self.assertFalse(primary["instrumento_canonico"])
        self.assertEqual(primary["tipo_variante_b3"], "ETF_PRIMARY_MARKET")
        self.assertIn(primary["ticker_canonico"], normal_tickers)

    def test_fixed_income_market_is_not_generically_promoted_to_etf(self):
        unpaired = instrument(
            "TEST11",
            "FIXED INCOME TRADABLE INSTRUMENT T1",
            segment="FORWARD",
            market="FIXED INCOME",
            isin="BRTESTCTF001",
        )

        annotate_universe([unpaired], REFERENCE_DATE)

        self.assertFalse(unpaired["instrumento_canonico"])
        self.assertEqual(unpaired["classe_preliminar"], "OUTRO")
        self.assertEqual(
            unpaired["tipo_variante_b3"],
            "ETF_RENDA_FIXA_SEM_VINCULO_OFICIAL",
        )

    def test_missing_start_preserves_structural_canonicity(self):
        pending = instrument("003H11", "FUNDS", isin="BR003HCTF006")
        pending["data_inicio_negociacao"] = None
        pending["data_fim_negociacao"] = None

        annotate_universe([pending], REFERENCE_DATE)

        self.assertTrue(pending["instrumento_canonico"])
        self.assertFalse(pending["corrente"])
        self.assertFalse(pending["atividade_confirmada_b3"])
        self.assertEqual(
            pending["status_atividade_b3"], "PENDENTE_DATA_INICIO"
        )
        self.assertIsNone(pending["tipo_variante_b3"])
        self.assertEqual(
            pending["motivo_validacao_b3"],
            "DATA_INICIO_NAO_INFORMADA_B3",
        )

    def test_future_trading_start_is_not_current(self):
        future = instrument("FUTR11", "ETF EQUITIES")
        future["data_inicio_negociacao"] = date(2026, 3, 1)

        annotate_universe([future], REFERENCE_DATE)

        self.assertTrue(future["instrumento_canonico"])
        self.assertFalse(future["corrente"])
        self.assertFalse(future["atividade_confirmada_b3"])
        self.assertEqual(future["status_atividade_b3"], "INICIO_FUTURO")
        self.assertIsNone(future["tipo_variante_b3"])
        self.assertEqual(
            future["motivo_validacao_b3"],
            "INICIO_NEGOCIACAO_FUTURO",
        )

    def test_expired_or_ended_instrument_is_inactive_b3(self):
        ended = instrument("OLD11", "FUNDS", isin="BROLD1CTF001")
        ended["data_fim_negociacao"] = date(2026, 2, 10)

        annotate_universe([ended], REFERENCE_DATE)

        self.assertTrue(ended["instrumento_canonico"])
        self.assertFalse(ended["atividade_confirmada_b3"])
        self.assertEqual(ended["status_atividade_b3"], "INATIVA_B3")
        self.assertEqual(ended["motivo_atividade_b3"], "INATIVO_B3")

    def test_etf_name_with_future_word_is_not_a_derivative(self):
        etf = instrument(
            "QFOF11",
            "ETF FOREIGN INDEX",
            corporate_name="QR BLOOMBERG FUTURE OF FINANCE FUNDO DE ÍNDICE",
        )

        annotate_universe([etf], REFERENCE_DATE)

        self.assertTrue(etf["instrumento_canonico"])
        self.assertEqual(etf["classe_preliminar"], "ETF")

    def test_normalize_reads_official_tradg_date_columns(self):
        normalized = normalize(
            {
                "RptDt": "2026-02-11",
                "TckrSymb": "FUTR11",
                "ISIN": "BRFUTRCTF001",
                "TradgStartDt": "9999-12-31",
                "TradgEndDt": "9999-12-31",
            },
            REFERENCE_DATE,
            "Final",
        )

        self.assertIsNone(normalized["data_inicio_negociacao"])
        self.assertIsNone(normalized["data_fim_negociacao"])

    def test_requested_missing_start_examples_are_canonical_but_pending(self):
        instruments = [
            instrument("003H11", "FUNDS", isin="BR003HCTF006"),
            instrument("0FEA11", "FUNDS", isin="BR0FEACTF006"),
            instrument("2WAV3", "SHARES", isin="BR2WAVACNOR8"),
        ]
        for item in instruments:
            item["data_inicio_negociacao"] = None
            item["data_fim_negociacao"] = None

        annotate_universe(instruments, REFERENCE_DATE)
        audit = build_audit(instruments)

        self.assertTrue(all(item["instrumento_canonico"] for item in instruments))
        self.assertTrue(all(not item["corrente"] for item in instruments))
        self.assertEqual(audit["total_canonicos_confirmados_b3"], 0)
        self.assertEqual(audit["total_canonicos_pendentes_data_inicio"], 3)
        self.assertEqual(
            audit["distribuicao_pendentes_data_inicio_classe"],
            {"ACAO": 1, "FUNDO": 2},
        )

    def test_ticker_canonico_is_linked_only_by_unique_isin(self):
        instruments = [
            instrument("AFHI11", "FUNDS", isin="BRAFHICTF005"),
            instrument(
                "AFHI11M",
                "FUNDS",
                segment="Equity Block Trading Lot",
                isin="BRAFHICTF005",
            ),
        ]

        annotate_universe(instruments, REFERENCE_DATE)

        self.assertTrue(instruments[0]["instrumento_canonico"])
        self.assertEqual(instruments[0]["ticker_canonico"], "AFHI11")
        self.assertFalse(instruments[1]["instrumento_canonico"])
        self.assertEqual(instruments[1]["ticker_canonico"], "AFHI11")

    def test_audit_partitions_the_complete_snapshot(self):
        instruments = [
            instrument("PETR4", "SHARES", isin="BRPETRACNPR6"),
            instrument("SANB11", "UNIT", isin="BRSANBCDAM13"),
            instrument("HGLG11", "FUNDS", isin="BRHGLGCTF004"),
            instrument("TEST1", "RIGHTS", isin="BRTESTACNOR1"),
        ]
        annotate_universe(instruments, REFERENCE_DATE)

        audit = build_audit(instruments)

        self.assertEqual(audit["total_bruto_snapshot"], 4)
        self.assertEqual(audit["total_canonico"], 3)
        self.assertEqual(audit["total_nao_canonico"], 1)
        self.assertEqual(
            audit["distribuicao_canonica_classe"],
            {"ACAO": 2, "FUNDO": 1},
        )
        self.assertEqual(
            audit["distribuicao_nao_canonicos_motivo"],
            {"RIGHTS": 1},
        )


class ActivityGateAndSanityTest(unittest.TestCase):
    def test_absent_asset_is_marked_without_delete(self):
        normalized_sql = " ".join(UPDATE_ABSENT_SQL.split())

        self.assertIn("status_atividade_b3 = 'AUSENTE_B3_ATUAL'", normalized_sql)
        self.assertIn("atividade_confirmada_b3 = false", normalized_sql)
        self.assertIn("elegivel_analise = false", normalized_sql)
        self.assertIn("where not exists", normalized_sql)
        self.assertNotIn("delete", normalized_sql.lower())

    def test_official_eligible_asset_loses_eligibility_when_activity_stops(self):
        def result(was_eligible, validation_status, activity_confirmed):
            return bool(
                was_eligible
                and validation_status == "VALIDADO_OFICIAL"
                and activity_confirmed
            )

        self.assertFalse(result(True, "VALIDADO_OFICIAL", False))
        normalized_sql = " ".join(UPDATE_CANONICAL_SQL.split())
        self.assertIn("a.elegivel_analise = true", normalized_sql)
        self.assertIn("d.atividade_confirmada_b3 = true", normalized_sql)

    def test_activity_return_does_not_reactivate_eligibility(self):
        was_eligible = False
        activity_confirmed = True

        remains_eligible = was_eligible and activity_confirmed

        self.assertFalse(remains_eligible)
        normalized_sql = " ".join(UPDATE_CANONICAL_SQL.split())
        self.assertNotIn("then d.atividade_confirmada_b3", normalized_sql)

    def test_existing_asset_receives_b3_owned_field_updates(self):
        normalized_sql = " ".join(UPDATE_CANONICAL_SQL.split())

        for assignment in (
            "nome = coalesce(d.nome, a.nome)",
            "nome_pregao = coalesce(d.nome_pregao, a.nome_pregao)",
            "tipo_instrumento = d.categoria_b3",
            "categoria_b3 = d.categoria_b3",
            "segmento_b3 = d.segmento_b3",
            "mercado_b3 = d.mercado_b3",
            "moeda = coalesce(d.moeda, a.moeda)",
            "ticker_canonico = d.ticker",
            "data_referencia_b3 = d.data_referencia",
        ):
            self.assertIn(assignment, normalized_sql)
        self.assertIn(
            "when a.status_validacao = 'VALIDADO_OFICIAL' then a.classe",
            normalized_sql,
        )

    def test_sanity_failure_prevents_snapshot_and_master_mutations(self):
        fake_conn = mock.Mock()
        save_snapshot = mock.patch.object(job, "save_snapshot")
        validate_master = mock.patch.object(job, "validate_master")

        with (
            mock.patch.object(
                job, "load_latest_valid_snapshot_profile", return_value=None
            ),
            mock.patch.object(
                job,
                "validate_snapshot_sanity",
                side_effect=SnapshotSanityError("arquivo incompleto"),
            ),
            save_snapshot as save_mock,
            validate_master as master_mock,
        ):
            with self.assertRaises(SnapshotSanityError):
                process_snapshot(
                    fake_conn,
                    [instrument("PETR4", "SHARES")],
                    REFERENCE_DATE,
                    "Final",
                )

        save_mock.assert_not_called()
        master_mock.assert_not_called()
        fake_conn.commit.assert_not_called()

    def test_sanity_rejects_partial_or_structurally_incomplete_snapshot(self):
        sample = [instrument("PETR4", "SHARES")]
        annotate_universe(sample, REFERENCE_DATE)

        with (
            mock.patch.object(job, "MIN_SNAPSHOT_RECORDS", 1),
            mock.patch.object(
                job, "ESSENTIAL_B3_CATEGORIES", frozenset({"SHARES", "BDR"})
            ),
        ):
            with self.assertRaises(SnapshotSanityError):
                validate_snapshot_sanity(sample, "Parcial")

    def test_sanity_rejects_abnormal_volume_variation(self):
        sample = [instrument("PETR4", "SHARES")]
        annotate_universe(sample, REFERENCE_DATE)

        with (
            mock.patch.object(job, "MIN_SNAPSHOT_RECORDS", 1),
            mock.patch.object(
                job, "ESSENTIAL_B3_CATEGORIES", frozenset({"SHARES"})
            ),
            mock.patch.object(job, "MAX_SNAPSHOT_VARIATION_RATIO", 0.35),
        ):
            with self.assertRaises(SnapshotSanityError):
                validate_snapshot_sanity(
                    sample,
                    "Final",
                    previous_profile={"total_registros": 10},
                )

    def test_plausible_raw_snapshot_with_canonical_loss_is_rejected(self):
        previous = sanity_sample(
            {"ACAO": 10, "BDR": 10, "ETF": 10, "FUNDO": 10},
            noncanonical_count=60,
        )
        current = sanity_sample(
            {"ACAO": 5, "BDR": 5, "ETF": 5, "FUNDO": 5},
            noncanonical_count=80,
        )

        with mock.patch.object(job, "MIN_SNAPSHOT_RECORDS", 1):
            with self.assertRaisesRegex(
                SnapshotSanityError, r"sanidade\[total_canonicos\]"
            ):
                validate_snapshot_sanity(
                    current,
                    "Final",
                    previous_profile=job.snapshot_profile(previous),
                )

    def test_normal_canonical_total_with_class_loss_is_rejected(self):
        previous = sanity_sample(
            {"ACAO": 40, "BDR": 40, "ETF": 40, "FUNDO": 40}
        )
        current = sanity_sample(
            {"ACAO": 25, "BDR": 45, "ETF": 45, "FUNDO": 45}
        )

        with mock.patch.object(job, "MIN_SNAPSHOT_RECORDS", 1):
            with self.assertRaisesRegex(
                SnapshotSanityError,
                r"sanidade\[canonicos_confirmados_classe_ACAO\]",
            ):
                validate_snapshot_sanity(
                    current,
                    "Final",
                    previous_profile=job.snapshot_profile(previous),
                )

    def test_small_operational_variation_is_accepted(self):
        previous = sanity_sample(
            {"ACAO": 100, "BDR": 100, "ETF": 100, "FUNDO": 100}
        )
        current = sanity_sample(
            {"ACAO": 98, "BDR": 102, "ETF": 99, "FUNDO": 101}
        )

        with mock.patch.object(job, "MIN_SNAPSHOT_RECORDS", 1):
            result = validate_snapshot_sanity(
                current,
                "Final",
                previous_profile=job.snapshot_profile(previous),
            )

        self.assertTrue(result["valido"])

    def test_first_collection_without_baseline_is_accepted(self):
        current = sanity_sample(
            {"ACAO": 2, "BDR": 2, "ETF": 2, "FUNDO": 2}
        )

        with mock.patch.object(job, "MIN_SNAPSHOT_RECORDS", 1):
            result = validate_snapshot_sanity(
                current,
                "Final",
                previous_profile=None,
            )

        self.assertTrue(result["valido"])
        self.assertEqual(result["variacoes_percentuais_absolutas"], {})

    def test_activity_state_contract(self):
        current = instrument("PETR4", "SHARES")
        pending = instrument("003H11", "FUNDS")
        pending["data_inicio_negociacao"] = None

        self.assertEqual(
            activity_state(current, REFERENCE_DATE),
            (True, "CONFIRMADA", None),
        )
        self.assertEqual(
            activity_state(pending, REFERENCE_DATE),
            (
                False,
                "PENDENTE_DATA_INICIO",
                "DATA_INICIO_NAO_INFORMADA_B3",
            ),
        )

    def test_migration_view_requires_all_eligibility_gates(self):
        migration = MIGRATION_009.read_text(encoding="utf-8")
        view_sql = migration[migration.rindex("create or replace view") :]

        self.assertIn("where elegivel_analise = true", view_sql)
        self.assertIn("and instrumento_canonico = true", view_sql)
        self.assertIn("and atividade_confirmada_b3 = true", view_sql)
        self.assertIn("and status_validacao = 'VALIDADO_OFICIAL'", view_sql)

    def test_migration_preserves_view_order_and_appends_activity_columns(self):
        migration = MIGRATION_009.read_text(encoding="utf-8")
        view_sql = migration[migration.rindex("create or replace view") :]
        expected_order = [
            "id,",
            "ticker,",
            "nome,",
            "nome_pregao,",
            "classe,",
            "subclasse,",
            "tipo_instrumento,",
            "setor,",
            "segmento,",
            "categoria_b3,",
            "segmento_b3,",
            "mercado_b3,",
            "isin,",
            "codigo_cvm,",
            "moeda,",
            "status_validacao,",
            "elegivel_analise,",
            "fonte_cadastro,",
            "fonte_validacao,",
            "validado_em,",
            "atualizado_em,",
            "instrumento_canonico,",
            "tipo_variante_b3,",
            "ticker_canonico,",
            "atividade_confirmada_b3,",
            "status_atividade_b3,",
            "motivo_atividade_b3,",
            "data_referencia_b3,",
            "ultima_confirmacao_b3,",
            "verificado_b3_em",
        ]

        cursor = 0
        for column in expected_order:
            position = view_sql.find(f"    {column}\n", cursor)
            self.assertGreaterEqual(position, 0, column)
            cursor = position + len(column)

    def test_noncanonical_instrument_cannot_enter_analytical_view(self):
        migration = MIGRATION_009.read_text(encoding="utf-8")

        self.assertIn("and instrumento_canonico = true", migration)
        self.assertIn(
            "instrumento_canonico = true\n"
            "            and atividade_confirmada_b3 = true",
            migration,
        )

    def test_current_official_snapshot_reproduces_activity_audit(self):
        snapshot_path = os.environ.get("B3_AUDIT_SNAPSHOT_PATH")
        if not snapshot_path:
            self.skipTest("B3_AUDIT_SNAPSHOT_PATH não configurado")

        content = Path(snapshot_path).read_bytes()
        status, rows = parse_csv(content)
        ref = date(2026, 8, 28)
        instruments = [normalize(row, ref, status) for row in rows]
        annotate_universe(instruments, ref)
        sanity = validate_snapshot_sanity(
            instruments,
            status,
            previous_profile=job.snapshot_profile(instruments, ref),
            ref=ref,
        )
        audit = build_audit(instruments)

        self.assertTrue(sanity["valido"])
        self.assertEqual(status, "Final")
        self.assertEqual(audit["total_canonicos_confirmados_b3"], 2_861)
        self.assertEqual(audit["total_canonicos_pendentes_data_inicio"], 482)
        self.assertEqual(
            audit["distribuicao_status_atividade_canonicos"],
            {"CONFIRMADA": 2_861, "PENDENTE_DATA_INICIO": 482},
        )


if __name__ == "__main__":
    unittest.main()
