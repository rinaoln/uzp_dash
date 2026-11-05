"""Регрессия потери колонок при пустом срезе организаций на проме."""
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from uzp_dash.dashboards.tb_health import analyze, bank


def _row(**changes):
    row = dict(tb_id=18, new_gosb_id=1801, inn=1001, in_ref=True,
               emp_potential_qty=12.5, pipe_np=2.5, out_kept=0,
               fot_potential_amt=100000, avg_salary=50000,
               seg_name="КСБ", worked=True, any_success=True)
    row.update(changes)
    return row


def _bank(orgs):
    """Вход prepare без обращения к БД; расчёты остальных разделов замещены отдельно."""
    units = pd.DataFrame(columns=["level_name", "tb_id", "end_dt", "unit_id"])
    return SimpleNamespace(
        orgs=orgs, orgs_fc=pd.DataFrame(columns=["tb_id"]),
        verdict=pd.DataFrame(), trend=pd.DataFrame(),
        unit_seg=units, unit_tot=units, apparat=set(), fc_stats={}, params={},
        conv={"by_tb": {}, "sb": 1, "diag_tb": {}, "diag_sb": {}, "by_gosb": {}},
        act_tot=pd.DataFrame(columns=["tb_id"]),
        act_brk=pd.DataFrame(columns=["tb_id"]),
        dates=dict(ref_cur=date(2026, 11, 30), ref_closed=date(2026, 10, 31),
                   ref_yoy=date(2025, 10, 31), days_left=25))


class OrganizationSliceTests(unittest.TestCase):
    def _prepare(self, orgs):
        # _assemble строит независимые матрицы, структуру и перетоки. Оставляем
        # реальными prepare, отбор кандидатов, классификацию и сбор пула аудита.
        with patch.object(analyze, "_assemble", return_value=SimpleNamespace(seg_gaps={})) as assemble:
            result = analyze.prepare(_bank(orgs), 18, "ББ", "Тестовый банк")
        return result, assemble.call_args.args[8]

    def test_empty_object_mask_keeps_schema_through_prepare(self):
        orgs = pd.DataFrame(columns=list(_row()))
        self.assertEqual(orgs["in_ref"].dtype, object)
        result, selected = self._prepare(orgs)
        self.assertEqual(list(selected.columns), list(orgs.columns))
        self.assertEqual(result.ref_stats, {"n_detail": 0, "n_ref": 0})
        self.assertTrue(result.to_work.empty)
        self.assertIn("emp_potential_qty", result.to_work)

    def test_no_organizations_for_tb_keeps_schema(self):
        orgs = pd.DataFrame([_row(tb_id=40)])
        orgs["in_ref"] = orgs["in_ref"].astype(object)
        result, selected = self._prepare(orgs)
        self.assertTrue(selected.empty)
        self.assertIn("emp_potential_qty", selected)
        self.assertTrue(result.no_point.empty)

    def test_reference_filter_preserves_existing_potential(self):
        orgs = pd.DataFrame([_row(), _row(inn=1002, in_ref=False),
                             _row(inn=1003, in_ref=None)])
        before = orgs.copy(deep=True)
        result, selected = self._prepare(orgs)
        self.assertEqual(selected["inn"].tolist(), [1001])
        self.assertEqual(result.ref_stats, {"n_detail": 3, "n_ref": 1})
        self.assertEqual(result.no_point["impact_attract"].tolist(), [10])
        self.assertEqual(result.no_point["impact_fot_mln"].tolist(), [.08])
        pd.testing.assert_frame_equal(orgs, before)

    def test_empty_sql_result_gets_boolean_reference_flag(self):
        raw = pd.DataFrame(columns=list(_row()))
        prepared = bank._prepare_orgs(raw, set(), {})
        self.assertTrue(pd.api.types.is_bool_dtype(prepared["in_ref"]))
        self.assertEqual(list(prepared.columns), list(raw.columns))
        self.assertEqual(raw["in_ref"].dtype, object)

    def test_candidate_amounts_with_existing_data(self):
        orgs = pd.DataFrame([_row(), _row(inn=1002, pipe_np=20, out_kept=4)])
        candidates = analyze._candidates(orgs)
        self.assertEqual(candidates["impact_attract"].tolist(), [10, 0])
        self.assertEqual(candidates["impact_fl"].tolist(), [10, 4])
        self.assertEqual(candidates["lever"].tolist(), ["Привлечь", "Вернуть"])
        self.assertEqual(candidates["impact_fot_mln"].tolist(), [.08, .2])


if __name__ == "__main__":
    unittest.main()
