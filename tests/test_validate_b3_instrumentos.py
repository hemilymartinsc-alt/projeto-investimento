from __future__ import annotations

import sys
import types
import unittest
from datetime import date


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

from src.jobs.validate_b3_instrumentos import (  # noqa: E402
    annotate_universe,
    build_audit,
    canonical_decision,
    normalize,
    preliminary_classification,
)


REFERENCE_DATE = date(2026, 2, 11)


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


class CanonicalRulesTest(unittest.TestCase):
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
        self.assertIsNone(future["tipo_variante_b3"])
        self.assertEqual(
            future["motivo_validacao_b3"],
            "INICIO_NEGOCIACAO_FUTURO",
        )

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


if __name__ == "__main__":
    unittest.main()
