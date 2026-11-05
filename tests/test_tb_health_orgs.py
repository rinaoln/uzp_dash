"""Регрессия потери колонок при пустом срезе организаций на проме."""
from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from uzp_dash.dashboards.tb_health import analyze, bank, view


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


class StructureRenderingTests(unittest.TestCase):
    def _people_structure(self, orgs=None):
        flow = pd.DataFrame()
        flow.attrs["m_cur"] = "2026-10"
        flow.attrs["people"] = {"multi": pd.DataFrame([
            dict(tb_id=18, one_org=80, two_org=15, many_org=5)])}
        st = analyze._structure(orgs)
        st["people"] = analyze._people(SimpleNamespace(flow=flow), 18)
        return st

    def test_people_only_structure_renders_without_organization_totals(self):
        st = self._people_structure(pd.DataFrame())
        self.assertNotIn("n_org", st)
        html = view._structure_section(SimpleNamespace(structure=st))
        self.assertIn("Сколько за получателями людей", html)
        self.assertIn("Зарплату получают <b>100</b>", html)
        self.assertIn("по организациям за выбранный месяц не рассчитан", html)
        self.assertNotIn("Портфель собран из", html)

    def test_organizations_without_recipients_do_not_become_zero_totals(self):
        orgs = pd.DataFrame([dict(inn=1001, current_fl_qty=0)])
        html = view._structure_section(SimpleNamespace(structure=self._people_structure(orgs)))
        self.assertIn("Сколько за получателями людей", html)
        self.assertNotIn("Портфель собран из", html)

    def test_existing_organization_totals_are_preserved(self):
        orgs = pd.DataFrame([dict(inn=1001, current_fl_qty=12, total_emp_qty=20,
                                 current_fot_amt=600000, seg_name="КСБ")])
        html = view._structure_section(SimpleNamespace(structure=self._people_structure(orgs)))
        self.assertIn("Портфель собран из <b>1</b>", html)
        self.assertIn("<b>12</b> получателей", html)
        self.assertIn("Сколько за получателями людей", html)

    def test_empty_structure_and_month_metadata_only_hide_section(self):
        for st in ({}, {"people": {"month": "2026-10"}}):
            with self.subTest(structure=st):
                self.assertEqual(view._structure_section(SimpleNamespace(structure=st)), "")

    def test_full_tb_and_sb_html_with_empty_orgs_and_available_people(self):
        cols = list(_row()) + ["current_fl_qty", "total_emp_qty", "current_fot_amt",
                               "industry_name", "company_name"]
        b = _bank(pd.DataFrame(columns=cols))
        b.dates.update(out_label="10.2026, 09.2026, 08.2026", label="11.2026",
                       closed_label="10.2026", ref_funnel=b.dates["ref_cur"])
        units = []
        for level, uid in (("gosb", 1801), ("tb", 18)):
            for dt in (b.dates["ref_cur"], b.dates["ref_closed"]):
                units.append(dict(level_name=level, tb_id=18, unit_id=uid,
                                  unit_name="Тестовое подразделение", end_dt=dt,
                                  seg_id=20, plan_amt=120, fact_amt=100, pred_amt=115))
        b.unit_seg = pd.DataFrame(units)
        b.unit_tot = b.unit_seg.copy()
        b.trend_seg = pd.DataFrame()
        b.orgs_tb = pd.DataFrame(columns=["tb_id", "inn"])
        b.orgs_fc["new_gosb_id"] = pd.Series(dtype="int64")
        b.seg_by_inn, b.tb_of = {}, {1801: 18}
        b.fagg, b.fmonths, b.promises = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        b.flow = pd.DataFrame()
        b.flow.attrs["people"] = {"multi": pd.DataFrame([
            dict(tb_id=18, one_org=80, two_org=15, many_org=5)])}
        b.flow.attrs["m_cur"] = "2026-10"

        # Полная цепочка анализа и HTML, без подмены функций и сетевых вызовов.
        tb = analyze.prepare(b, 18, "ББ", "Тестовый банк")
        analyze.finish(SimpleNamespace(params={"llm_max_calls": 0}), b, tb, pd.DataFrame())
        sb = analyze.build_sb(b, [tb])
        for idx, a in enumerate((sb, tb)):
            with self.subTest(level=a.level):
                html = view._level_body(a, {}, idx)
                self.assertIn("Сколько за получателями людей", html)
                self.assertIn("по организациям за выбранный месяц не рассчитан", html)
                self.assertNotIn("Портфель собран из", html)


if __name__ == "__main__":
    unittest.main()
