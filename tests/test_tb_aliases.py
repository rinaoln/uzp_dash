"""Названия ТБ возвращаются в выводы после изменения формы псевдонима моделью."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from uzp_dash import progress
from uzp_dash.anonymize import Aliases
from uzp_dash.dashboards.tb_health import prompts


BANKS = ("ББ", "ВВБ", "ДВБ", "МБ", "ПБ", "СЗБ", "СРБ", "СибБ", "УБ")
SCREENSHOT = (
    "Подразделения, где список организаций под план самый длинный, сосредоточены "
    "в территориальных банках 06 и 09. Отклонение закрывается отработкой списка "
    "организаций, потенциал которых покрывает разрыв на 120%. В первую очередь "
    "предлагается обеспечить привлечение потенциала по территориальному банку 06, "
    "где отобрано 1 416 организаций.")


class BankAliasTests(unittest.TestCase):
    def setUp(self):
        self.aliases = Aliases()
        for name in BANKS:
            self.aliases.alias("ТБ", name)

    def test_exact_screenshot_text_restores_both_bank_names(self):
        text = self.aliases.restore(SCREENSHOT)
        self.assertIn("в СЗБ и УБ", text)
        self.assertIn("по СЗБ,", text)
        self.assertIn("120%", text)
        self.assertIn("1 416 организаций", text)
        self.assertNotIn("06", text)
        self.assertNotIn("09", text)

    def test_inflected_full_bank_designations(self):
        for form in ("территориальный банк", "территориального банка",
                     "территориальному банку", "территориальным банком",
                     "территориальном банке", "территориальные банки",
                     "территориальных банках", "территориальных банков"):
            with self.subTest(form=form):
                self.assertEqual(self.aliases.restore(form + " 06"), "СЗБ")
        self.assertEqual(self.aliases.restore("ТЕРРИТОРИАЛЬНЫЙ БАНК № 06"), "СЗБ")

    def test_canonical_bank_gosb_and_company_aliases_still_restore(self):
        self.aliases.alias("ГОСБ", "Тестовый ГОСБ")
        self.aliases.alias("Организация", "Тестовая компания")
        self.assertEqual(self.aliases.restore("ТБ-06, 09; ГОСБ-01; Организации-01"),
                         "СЗБ, УБ; Тестовый ГОСБ; Тестовая компания")

    def test_dates_percentages_and_counts_are_not_bank_aliases(self):
        for text in ("План 120%, отобрано 1 416 организаций, 06.2026 и 09.2026.",
                     "территориальный банк 06.2026", "территориальный банк 6,5%",
                     "территориальный банк 6 %", "территориальный банк 6 организаций"):
            with self.subTest(text=text):
                self.assertEqual(self.aliases.restore(text), text)

    def test_unknown_number_is_not_assigned_to_an_existing_bank(self):
        text = self.aliases.restore("территориальному банку 99")
        self.assertEqual(text, "другие территориальные банки")
        self.assertEqual(self.aliases.leaked, 1)

    def test_full_bank_phrase_is_not_reinterpreted_without_bank_aliases(self):
        aliases = Aliases()
        aliases.alias("ГОСБ", "Тестовый ГОСБ")
        text = "территориальный банк 06"
        self.assertEqual(aliases.restore(text), text)

    def test_section_narrative_restores_names_in_actual_response_pipeline(self):
        row = dict(fact=100, plan=100, exec=1, rank=1, n_tb=9)
        cards = [dict(gosb_name=name, segs=[], n_need=0, fl_need=0) for name in BANKS]
        a = SimpleNamespace(verdict={"rcp": row, "fot": row}, unit_label="ТБ", level="sb",
                            gosb_cards=cards, top_cells=pd.DataFrame(), to_work=pd.DataFrame(),
                            pf={}, dates={}, closed={}, tb_full="Сбербанк", ref_date="11.2026",
                            activity={}, themes="—", no_point=pd.DataFrame(), outflow={},
                            sim={k: 0 for k in ("k", "closable", "fot_mln", "attract",
                                                "retention", "coverage")})
        llm = Mock(return_value='[[gosb]]\n' + SCREENSHOT)
        with patch.object(progress, "LOG_DIR", None), patch.object(prompts, "_USED_OPENERS", {}):
            result = prompts.section_narratives(SimpleNamespace(llm=llm), a)
        self.assertIn("в СЗБ и УБ", result["gosb"])
        self.assertIn("по СЗБ,", result["gosb"])
        self.assertIn("120%", result["gosb"])
        self.assertNotIn("территориальных банках 06", result["gosb"])
        prompt = llm.call_args.args[0]
        self.assertIn("Обозначения вида ТБ-01", prompt)
        for name in ("СЗБ", "СибБ", "УБ"):
            self.assertNotIn(name, prompt)


if __name__ == "__main__":
    unittest.main()
