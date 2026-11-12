"""Поздняя поставка организаций не сдвигает месяц витринного прогноза."""
from datetime import date
from types import CodeType, SimpleNamespace
import sqlite3
import unittest
import builtins
import dis
import inspect
from unittest.mock import patch

import pandas as pd

from uzp_dash.dashboards.tb_health import analyze, bank, queries as Q, view, visual
from uzp_dash.dashboards.tb_health.freshness import orgs_snapshot


class LatestOrganizationSourceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE uzp_dwh_metrics (end_dt TEXT, period_type TEXT,
                metric_id INT, level_name TEXT, prediction_amt REAL);
            INSERT INTO uzp_dwh_metrics VALUES ('2026-11-30','m',1020377,'sb',115);
            CREATE TABLE uzp_dwh_company_holding_metric (
                report_dt TEXT, level_name TEXT, org_type TEXT, level_id INT, org_id INT,
                current_fl_qty REAL DEFAULT 6, current_fot_amt REAL DEFAULT 300000,
                total_emp_qty REAL DEFAULT 20, fl_y_1_diff_qty REAL DEFAULT -4,
                emp_potential_qty REAL DEFAULT 12.5, fot_potential_amt REAL DEFAULT 625000,
                fl_outflow_qty REAL DEFAULT 0, fot_outflow_amt REAL DEFAULT 0);
            INSERT INTO uzp_dwh_company_holding_metric (report_dt,level_name,org_type,level_id,org_id)
                VALUES ('2026-08-31','gosb','inn',1,1001),
                       ('2026-09-30','gosb','inn',1,1001),
                       ('2026-11-30','gosb','inn',1,1001),
                       ('2026-10-31','gosb','holding',1,1001),
                       ('2026-09-30','tb','inn',18,1001),
                       ('2026-10-31','tb','inn',18,1001),
                       ('2026-11-30','tb','inn',18,1001);
            CREATE TABLE uzp_dim_gosb (old_gosb_id INT, tb_id INT, new_gosb_id INT,
                new_gosb_name TEXT, tb_short_name TEXT);
            INSERT INTO uzp_dim_gosb VALUES (1,18,1801,'Тестовый ГОСБ','ББ');
            CREATE TABLE uzp_dim_company (inn INT, company_name TEXT, segment_name TEXT);
            INSERT INTO uzp_dim_company VALUES (1001,'Тестовая организация','КСБ');
            CREATE TABLE uzp_dim_mzp_reference_base (inn INT, gosb_id INT);
            INSERT INTO uzp_dim_mzp_reference_base VALUES (1001,1801);
        """)

    def _read(self, engine, sql, params=None):
        values = {k: v.isoformat() if isinstance(v, date) else v
                  for k, v in (params or {}).items()}
        return pd.read_sql_query(sql.format(schema="main"), self.db, params=values)

    def test_november_forecast_uses_latest_eligible_source_for_each_level(self):
        with patch.object(bank, "read_sql", side_effect=self._read):
            dates = bank.dates(self.db, {"report_month": "2026-11", "today": "2026-11-05"})
            dates.update(bank.organization_dates(self.db, dates["ref_closed"]))
        self.assertEqual(dates["ref_cur"], date(2026, 11, 30))
        self.assertEqual(dates["ref_closed"], date(2026, 10, 31))
        self.assertEqual(dates["ref_orgs"], date(2026, 9, 30))
        self.assertEqual(dates["ref_orgs_tb"], date(2026, 10, 31))
        orgs = self._read(self.db, Q.ORGS_ALL, {"ref_orgs": dates["ref_orgs"]})
        tb = self._read(self.db, Q.ORGS_TB, {"ref_orgs_tb": dates["ref_orgs_tb"]})
        self.assertEqual(len(orgs), 1)
        self.assertEqual(orgs.loc[0, "emp_potential_qty"], 12.5)
        self.assertEqual(orgs.loc[0, "fl_yoy"], -4)
        self.assertEqual(len(tb), 1)
        self.assertTrue(orgs_snapshot(dates)["stale"])
        self.assertFalse(orgs_snapshot(dates, "tb")["stale"])

    def test_newly_arrived_closed_month_is_selected_automatically(self):
        self.db.execute("""INSERT INTO uzp_dwh_company_holding_metric
            (report_dt,level_name,org_type,level_id,org_id)
            VALUES ('2026-10-31','gosb','inn',1,1001)""")
        with patch.object(bank, "read_sql", side_effect=self._read):
            dates = bank.organization_dates(self.db, date(2026, 10, 31))
        self.assertEqual(dates["ref_orgs"], date(2026, 10, 31))

    def test_historical_report_never_uses_future_source(self):
        with patch.object(bank, "read_sql", side_effect=self._read):
            dates = bank.organization_dates(self.db, date(2026, 8, 31))
        self.assertEqual(dates["ref_orgs"], date(2026, 8, 31))
        self.assertIsNone(dates["ref_orgs_tb"])

    def test_no_source_retains_empty_schema_instead_of_reading_another_period(self):
        with patch.object(bank, "read_sql", side_effect=self._read):
            dates = bank.organization_dates(self.db, date(2026, 7, 31))
        self.assertIsNone(dates["ref_orgs"])
        orgs = self._read(self.db, Q.ORGS_ALL, {"ref_orgs": dates["ref_orgs"]})
        self.assertTrue(orgs.empty)
        self.assertIn("emp_potential_qty", orgs)

    def test_loaded_older_organizations_reach_november_potential_and_matrix(self):
        from test_tb_health_orgs import _bank
        with patch.object(bank, "read_sql", side_effect=self._read):
            dates = bank.dates(self.db, {"report_month": "2026-11", "today": "2026-11-05"})
            dates.update(bank.organization_dates(self.db, dates["ref_closed"]))
        raw = self._read(self.db, Q.ORGS_ALL, {"ref_orgs": dates["ref_orgs"]})
        orgs = bank._prepare_orgs(raw, set(), {1801: 18})
        orgs = bank._merge_funnel(orgs, pd.DataFrame())
        orgs["pipe_np"], orgs["out_kept"] = 0., 0.
        orgs["seg_name"] = "КСБ"
        b = _bank(orgs)
        b.dates = dates
        b.orgs_tb = bank._prepare_orgs_tb(self._read(
            self.db, Q.ORGS_TB, {"ref_orgs_tb": dates["ref_orgs_tb"]}), orgs)
        units = [dict(level_name=level, tb_id=18, unit_id=uid, unit_name="Тестовый ГОСБ",
                      end_dt=dt, seg_id=20, plan_amt=120, fact_amt=100, pred_amt=115)
                 for level, uid in (("gosb", 1801), ("tb", 18))
                 for dt in (dates["ref_cur"], dates["ref_closed"])]
        b.unit_seg, b.unit_tot = pd.DataFrame(units), pd.DataFrame(units)
        b.verdict = pd.DataFrame([
            dict(level_name=level, level_id=uid, end_dt=dt, metric_id=mid,
                 plan_amt=120, fact_amt=100, pred_amt=115, execution_percent=100/120,
                 rnk=1, n_tb=1)
            for level, uid in (("tb", 18), ("sb", 0))
            for dt in (dates["ref_cur"], dates["ref_closed"])
            for mid in (Q.METRIC_RECIPIENTS, Q.METRIC_FOT)])
        b.trend_seg, b.fagg, b.fmonths = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        b.promises, b.flow = pd.DataFrame(), pd.DataFrame()
        b.tb_of, b.seg_by_inn = {1801: 18}, {1001: "КСБ"}
        b.orgs_fc = pd.DataFrame([dict(
            tb_id=18, new_gosb_id=1801, inn=1001, out_qty=5., ret_qty=1., out_kept=4.,
            out_months=["10.2026"], pipe_np=0., pipe_np_raw=0., pipe_fact_mtd=0.)])
        tb = analyze.prepare(b, 18, "ББ", "Тестовый банк")
        analyze.finish(SimpleNamespace(params={"llm_max_calls": 0}), b, tb, pd.DataFrame())
        sb = analyze.build_sb(b, [tb])
        self.assertFalse(tb.to_work.empty)
        self.assertIn((1801, "КСБ"), tb.cell_top)
        self.assertIn((18, "КСБ"), sb.cell_top)
        for idx, a in enumerate((sb, tb)):
            with self.subTest(level=a.level):
                self.assertTrue(a.gosb_detail)
                html = view._level_body(a, {}, idx)
                self.assertIn("Прогноз на 11.2026", html)
                self.assertIn("30.09.2026", html)
                self.assertLess(html.index('class="pd-health"'), html.index('class="pd-metrics"'))
                self.assertEqual(a.verdict["rcp"]["fact"], 115)
                self.assertIn(f'id="gd-{idx}-{18 if idx == 0 else 1801}"', html)
                expected = "10.2026 к 10.2025" if idx == 0 else "09.2026 к 09.2025"
                self.assertIn(expected, html)


class SourceDateRenderingTests(unittest.TestCase):
    def setUp(self):
        self.dates = dict(ref_cur=date(2026, 11, 30), ref_closed=date(2026, 10, 31),
                          ref_orgs=date(2026, 9, 30), ref_orgs_tb=date(2026, 8, 31),
                          closed_label="10.2026", label="11.2026")

    def test_decline_period_follows_source_date(self):
        a = SimpleNamespace(dates=self.dates, level="tb",
                            detail=pd.DataFrame([{"fl_yoy": -4}]))
        meta = view._cell_meta(a)
        self.assertTrue(meta["available"])
        self.assertIn("30.09.2026", meta["source_note"])
        self.assertIn("09.2026 к 09.2025", meta["source_note"])
        a.level = "sb"
        self.assertIn("08.2026 к 08.2025", view._cell_meta(a)["source_note"])

    def test_missing_source_is_not_labeled_as_current(self):
        info = orgs_snapshot({**self.dates, "ref_orgs": None})
        self.assertFalse(info["available"])
        self.assertIn("не найден", info["note"])
        self.assertEqual(info["yoy"], "")

    def test_health_panel_is_before_kpis_and_cannot_be_collapsed(self):
        a = SimpleNamespace(dates=self.dates, ref_date="11.2026", tb_full="Тестовый банк", unit_label="ТБ",
                            verdict={"rcp": {"fact": 115, "plan": 120, "exec": 115/120},
                                     "fot": {"fact": 1000000, "plan": 1000000, "exec": 1}},
                            trend=[], yoy={}, gosb_cards=[], outflow={})
        health = '<div class="hx-wrap"><span class="hx-eyebrow">Здоровье портфеля</span></div>'
        html = visual.overview(a, 0, {}, {"fl_need": 10, "k": 1}, None, [], health=health)
        self.assertLess(html.index('class="pd-health"'), html.index('class="pd-metrics"'))
        self.assertIn('<section class="pd-health"', html)
        self.assertNotIn('<details class="pd-health"', html)
        self.assertIn("30.09.2026", html)
        self.assertIn("Прогноз на 11.2026", html)

    def test_view_functions_have_no_undefined_global_names(self):
        known = set(vars(view)) | set(vars(builtins))
        errors = []

        def check(code):
            for instruction in dis.get_instructions(code):
                if instruction.opname == "LOAD_GLOBAL" and instruction.argval not in known:
                    errors.append(f'{code.co_name}: {instruction.argval}')
            for child in code.co_consts:
                if isinstance(child, CodeType):
                    check(child)

        for value in vars(view).values():
            if inspect.isfunction(value) and value.__module__ == view.__name__:
                check(value.__code__)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
