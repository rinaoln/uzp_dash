"""Сопоставимость периодов, полный отток и строгая связь задач с когортой ухода."""
from datetime import date
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from uzp_dash.dashboards.tb_health import analyze, outflows as O, outflow_view as V, queries as Q, view, prompts


def source_fixture():
    available = {date(y, m, 31 if m in (8, 10) else 30) for y in (2025, 2026) for m in (8, 9, 10)}
    dates = O.period_dates(date(2026, 10, 31), available)
    records = []
    for year, month, gosb, inn, gone, returned in [
        (2026, 10, 1, 101, 100, 30), (2026, 9, 1, 101, 20, 10),
        (2026, 8, 2, 102, 40, 10), (2026, 10, 1, 103, 2, 0),
        (2026, 10, 2, 103, 2, 0), (2026, 10, 1, 104, 10, 10),
        (2025, 10, 1, 101, 80, 20), (2025, 9, 1, 101, 10, 0),
        (2025, 8, 2, 102, 30, 5), (2025, 10, 1, 103, 1, 0),
    ]:
        records.append(dict(report_dt=(pd.Timestamp(year, month, 1)+pd.offsets.MonthEnd(0)).date(),
                            old_gosb_id=gosb, new_gosb_id=gosb, tb_id=18,
                            gosb_name=f"ГОСБ {gosb}", region="Область А" if gosb == 1 else "Область Б",
                            inn=inn, company_name=f"Клиент {inn}", seg="РГС", gone=gone,
                            returned=returned, return_record=True, return_source=True))
    tasks = []
    for code, month, gosb, inn, role, kind, success in [
        ("t1", 10, 1, 101, "МЗП", "Отток", False),
        ("t2", 9, 1, 101, "СЗП", "Отток", True),
        ("t3", 9, 2, 102, "МКК", "Отток", True),
        ("contact", 10, 1, 104, "МЗП", "Контактная политика", True),
        ("early", 7, 2, 102, "МЗП", "Отток", False),
        ("late", 12, 1, 101, "МЗП", "Отток", False),
        ("other", 10, 1, 999, "МЗП", "Отток", False),
    ]:
        tasks.append(dict(task_code=code, report_dt=date(2026, month, 20),
                          task_create_dt=date(2026, month, 15), old_gosb_id=gosb,
                          new_gosb_id=gosb, tb_id=18, inn=inn, company_name=f"Клиент {inn}",
                          role_code=role, task_type=kind,
                          is_task_closed=success, is_task_closed_success=success,
                          plan_close_task_dttm=date(2026, month, 18),
                          task_questionnaire="1. Причина оттока\nУшли в другой банк",
                          task_comment="Администрация перевела финансирование в другой банк." if code == "t1" else ""))
    # Повторный снимок не должен удвоить ни покрытие, ни роль, ни число задач.
    tasks.insert(0, {**tasks[0], "report_dt": date(2026, 10, 16), "role_code": "СЗП"})
    return O.Source(pd.DataFrame(records), pd.DataFrame(tasks), dates, available, True)


class OutflowAnalysisTests(unittest.TestCase):
    def report(self, source=None):
        return O.analyze(source or source_fixture(), None, date(2026, 11, 6))

    def test_total_adds_b2c_without_reclassifying_after_rollup(self):
        o = self.report()
        self.assertEqual(o["total"], 174)
        self.assertEqual(o["b2b"], 170)
        self.assertEqual(o["b2c"], 4)
        self.assertEqual(o["total"], o["b2b"]+o["b2c"])

    def test_yoy_uses_same_three_months_and_handles_zero_base(self):
        o = self.report()
        self.assertEqual(o["change"]["previous"], 121)
        self.assertEqual(o["change"]["absolute"], 53)
        self.assertAlmostEqual(o["change"]["percent"], 53/121*100)
        self.assertIsNone(O.change(3, 0)["percent"])
        self.assertIn("база 0", V.delta(O.change(3, 0)))

    def test_b2b_goal_includes_full_returns_and_excludes_b2c(self):
        o = self.report()
        self.assertEqual(o["returned"], 60)
        self.assertEqual(o["return_target"], 85)
        self.assertAlmostEqual(o["execution"], 60/85*100)
        self.assertAlmostEqual(o["return_rate"], 60/170*100)
        self.assertEqual(o["return_gap"], 25)

    def test_returns_cannot_exceed_cohort_outflow(self):
        s = source_fixture()
        s.facts.loc[s.facts.inn.eq(104), "returned"] = 1000
        self.assertEqual(self.report(s)["returned"], 60)

    def test_missing_partition_never_means_zero_yoy(self):
        s = source_fixture()
        s.available.remove(date(2025, 9, 30))
        o = self.report(s)
        self.assertFalse(o["comparable"])
        self.assertIsNone(o["change"]["absolute"])
        self.assertTrue(all(r["change"]["previous"] is None for r in o["reasons"]))
        s.available.remove(date(2026, 9, 30))
        o = self.report(s)
        self.assertFalse(o["complete"])
        self.assertIn("Неполное окно", V.overview(o))

    def test_missing_returns_and_tasks_are_unavailable_not_failed(self):
        s = source_fixture()
        s.facts["return_source"] = False
        s.facts["returned"] = 0
        s.tasks_ok = False
        o = self.report(s)
        self.assertIsNone(o["execution"])
        self.assertIn("Данные возврата ещё не поступили", V.overview(o))
        self.assertIn("Данные задач недоступны", V.tasks(o))
        self.assertNotIn("Цель пока не достигнута", V.overview(o))

    def test_unique_tasks_in_m_or_next_month_and_role_structure(self):
        o = self.report()
        t = o["tasks"]
        self.assertEqual((t["clients"], t["covered"], t["tasks"]), (3, 2, 3))
        self.assertEqual(t["covered_gone"], 160)
        self.assertAlmostEqual(t["percent"], 2/3*100)
        self.assertEqual({r["role"]: r["tasks"] for r in t["roles"] if r["tasks"]},
                         {"МЗП": 1, "СЗП": 1, "МКК": 1})
        # все роли воронки видны, даже без задач
        self.assertTrue({"НО", "НУ", "ДУ", "УПР", "ЗАМПРЕД"} <= {r["role"] for r in t["roles"]})
        mzp = next(r for r in t["roles"] if r["role"] == "МЗП")
        self.assertEqual((mzp["fl"], mzp["fl_returned"]), (120, 40))
        self.assertEqual(sum(r["overdue"] for r in t["roles"]), 1)
        for r in t["roles"]:
            self.assertEqual(r["tasks"], r["success"]+r["closed_other"]+r["open"])

    def test_region_and_reason_totals_reconcile_and_context_is_escaped(self):
        o = self.report()
        self.assertEqual(sum(r["gone"] for r in o["regions"]), o["b2b"])
        self.assertEqual(sum(r["gone"] for r in o["reasons"]), o["b2b"])
        self.assertEqual(sum(r["change"]["previous"] for r in o["reasons"]), 120)
        o["cases"][0]["name"] = '<script>alert("x")</script>'
        rendered = V.concentration(o)
        self.assertNotIn('<script>alert', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("финансирование", rendered)

    def test_known_empty_tb_is_zero_and_unknown_source_is_not_zero(self):
        o = O.analyze(source_fixture(), 40, date(2026, 11, 6))
        self.assertEqual(o["total"], 0)
        self.assertTrue(o["available"])
        self.assertIsNone(o["execution"])
        missing = O.analyze(O.Source(error="Нет среза"), None, date(2026, 11, 6))
        self.assertFalse(missing["available"])
        self.assertIn("Нет среза", V.overview(missing))

    def test_section_exists_for_b2c_only_or_fully_returned_and_stats_precede_lists(self):
        o = self.report()
        a = SimpleNamespace(outflow={"analytics": o}, unit_label="ТБ", dates={})
        rendered = view._outflow_section(a)
        self.assertLess(rendered.index("Общий отток получателей"), rendered.index("Концентрация оттоков"))
        self.assertIn("Отток год назад", rendered)
        self.assertIn("Покрытие задачами", rendered)
        self.assertLess(rendered.index("Фокусы внимания руководителя"), rendered.index("Общий отток получателей"))
        self.assertIn("B2C не входит в цель", prompts._outflow_fallback(a))

    def test_same_inn_in_two_gosbs_is_one_client_at_sb_and_two_at_tb(self):
        s = source_fixture()
        extra = s.facts.loc[s.facts.inn.eq(101)].iloc[0].to_dict()
        extra.update(new_gosb_id=2, old_gosb_id=2, gone=3, returned=0, gosb_name="ГОСБ 2")
        s.facts = pd.concat([s.facts, pd.DataFrame([extra])], ignore_index=True)
        sb = O.analyze(s, None, date(2026, 11, 6))
        tb = O.analyze(s, 18, date(2026, 11, 6))
        self.assertEqual(sb["tasks"]["clients"], 3)
        self.assertEqual(tb["tasks"]["clients"], 4)

    def test_missing_text_fields_keep_honest_labels(self):
        s = source_fixture()
        s.facts["company_name"], s.facts["region"] = pd.NA, pd.NA
        s.tasks["company_name"], s.tasks["role_code"] = pd.NA, " "
        o = self.report(s)
        self.assertEqual(o["regions"][0]["name"], O.NO_REGION)
        self.assertEqual(o["tasks"]["roles"][0]["role"], "Роль не указана")
        self.assertNotIn('>nan<', V.concentration(o))

    def test_new_block_attaches_even_when_legacy_has_no_losses(self):
        source = source_fixture()
        a = SimpleNamespace(outflow={}, level="sb", tb_id=0)
        b = SimpleNamespace(outflow_source=source, params={},
                            dates=dict(ref_closed=date(2026,10,31),today=date(2026,11,6)))
        analyze._attach_outflow_analytics(a,b)
        a.tb_short = "СБ"
        analyze._log_outflow_section(a)
        self.assertEqual(a.outflow["analytics"]["total"], 174)


class LeaderFocusTests(unittest.TestCase):
    def source(self):
        s = source_fixture()
        s.dates["out_t2"] = date(2026, 9, 30)
        # t2 закрыта как «временное снижение», а из 20 ушедших вернулись 10
        s.tasks.loc[s.tasks.task_code.eq("t2"), "task_comment"] = (
            "Причина оттока: сезонные отпуска, снижение временное, клиент обслуживание не менял")
        s.tasks.loc[s.tasks.task_code.eq("t2"), "isu_struct_saphr_id"] = 777
        s.tasks.loc[s.tasks.task_code.eq("t2"), "role_code"] = "МЗП"
        return s

    def test_unconfirmed_rule(self):
        from uzp_dash.dashboards.tb_health import text_rules as T
        self.assertTrue(T.outflow_unconfirmed("1. Причина оттока\nСезонные отпуска"))
        self.assertTrue(T.outflow_unconfirmed("", "Отток не подтвержден, портфель на месте"))
        self.assertTrue(T.outflow_unconfirmed("", "сотрудники вернутся в декабре"))
        self.assertFalse(T.outflow_unconfirmed("1. Причина оттока\nСокращение штата"))
        self.assertFalse(T.outflow_unconfirmed("", "Клиент подтвердил уход"))
        self.assertFalse(T.outflow_unconfirmed("", "сотрудники не вернутся"))

    def test_t2_rating_uses_fact_of_return(self):
        o = O.analyze(self.source(), None, date(2026, 11, 6))
        t = o["leader"]["t2"]
        self.assertEqual(t["month_label"], "09.2026")
        self.assertEqual((t["gone"], t["kept"]), (20, 10))
        self.assertEqual(t["roles"][0]["name"], "МЗП")
        self.assertEqual(t["mzp"][0]["name"], "таб. № 777")
        self.assertAlmostEqual(t["units"][0]["share"], 50)
        html = V.leader(o, "ТБ")
        self.assertIn("Фокусы внимания руководителя", html)
        self.assertIn("таб. № 777", html)

    def test_top_cases_patterns_segments_cards(self):
        o = O.analyze(self.source(), None, date(2026, 11, 6))
        top = o["leader"]["top"]
        self.assertEqual(top[0]["inn"], 101)
        self.assertEqual(top[0]["kept"], 80)
        self.assertEqual(top[0]["streak"], 2)
        # клиент 101 уходит два месяца подряд с возвратом 33% < 50%
        self.assertTrue(any(p["dim"] == "client" and p["streak"] == 2
                            for p in o["leader"]["patterns"]))
        b2b = o["segments"]["B2B"]["rows"]
        self.assertEqual(b2b[-1]["name"], "Итого")
        self.assertEqual(b2b[-1]["gone"], o["b2b"])
        self.assertEqual(b2b[-1]["gap"], 60 - 85)
        self.assertEqual(o["segments"]["B2C"]["target"], .15)
        titles = [c["title"] for c in o["cards"]]
        self.assertIn("Территориальные банки", titles)
        self.assertIn("Оттоки по сегментам", V.segments(o))
        self.assertIn("топ-5", V.concentration(o))


class OutflowFixesTests(unittest.TestCase):
    """Группировка причин, регион, детализация закономерностей, роли сервиса."""

    def test_reasons_with_same_start_are_one_reason(self):
        from uzp_dash.dashboards.tb_health import text_rules as T
        raw = ["Переток в другую организацию", "Переток в другую организацию (холдинг)",
               "переток в другую организацию: ИНН 7701234567",
               "Переток в другую организацию\nсотрудники перешли в ООО",
               "Переток в другую организацию группы компаний",
               "Ликвидация организации", "Ликвидация",
               "Переход сотрудников на самозанятость", "Переход сотрудников в другую организацию"]
        labels = {}
        for r in raw:
            labels[T._reason_label(r)] = labels.get(T._reason_label(r), 0) + 1
        g = T.group_reasons(labels)
        self.assertEqual({g[T._reason_label(r)] for r in raw[:5]}, {"Переток в другую организацию"})
        self.assertEqual(g["Ликвидация организации"], "Ликвидация")
        self.assertNotEqual(g["Переход сотрудников на самозанятость"],
                            g["Переход сотрудников в другую организацию"])
        # общего полного варианта нет — имя группы помечено многоточием
        g2 = T.group_reasons({"Переток в другую организацию холдинга": 2,
                              "Переток в другую организацию ИП": 1})
        self.assertEqual(set(g2.values()), {"Переток в другую организацию…"})

    def test_reason_summary_has_no_duplicates(self):
        s = source_fixture()
        s.tasks["task_questionnaire"] = [
            "1. Причина оттока\nПереток в другую организацию (холдинг)",
            "1. Причина оттока\nПереток в другую организацию: ИНН 1",
            "1. Причина оттока\nПереток в другую организацию",
            "1. Причина оттока\nПереток в другую организацию\nкомментарий",
            "", "", "", ""]
        o = O.analyze(s, None, date(2026, 11, 6))
        names = [r["name"] for r in o["reasons"]]
        self.assertEqual([n for n in names if n.startswith("Переток")],
                         ["Переток в другую организацию"])

    def test_region_fallback_for_moscow_and_empty_dictionary(self):
        s = source_fixture()
        s.facts["region"] = pd.NA
        s.facts.loc[s.facts.old_gosb_id.eq(1), ["old_gosb_id", "new_gosb_id"]] = 9038
        o = O.analyze(s, None, date(2026, 11, 6))
        names = {r["name"] for r in o["regions"]}
        self.assertIn("г. Москва", names)
        self.assertIn(O.NO_REGION, names)

    def test_pattern_rows_open_organization_detail(self):
        o = O.analyze(source_fixture(), None, date(2026, 11, 6))
        pat = next(p for p in o["leader"]["patterns"] if p["dim"] == "client")
        self.assertEqual(pat["detail"][0]["inn"], 101)
        self.assertEqual(pat["detail"][0]["kept"], 80)
        html = V.leader(o, "ТБ")
        self.assertIn('class="oa-pat"', html)
        self.assertIn("детализация", html)

    def test_service_roles_note_explains_empty_rows(self):
        s = source_fixture()
        s.service = pd.DataFrame([
            dict(tb_id=18, role_code="РН по Сервису", task_type="Сервисная задача по Юр.лицу",
                 task_code="s1", same_gosb=1),
            dict(tb_id=18, role_code="МС-СМС", task_type="Отток", task_code="s2", same_gosb=0),
            dict(tb_id=40, role_code="МС-СМС", task_type="Отток", task_code="s3", same_gosb=1)])
        o = O.analyze(s, 18, date(2026, 11, 6))
        svc = o["tasks"]["service"]
        self.assertEqual((svc["total"], svc["outflow"], svc["outflow_same"]), (2, 1, 0))
        html = V.tasks(o)
        self.assertIn("Роли сервиса", html)
        self.assertIn("Сервисная задача по Юр.лицу", html)
        self.assertIn("на другой ГОСБ", html)
        # диагностика не читалась — пояснения нет, а не «задач нет»
        self.assertNotIn("Роли сервиса", V.tasks(O.analyze(source_fixture(), 18, date(2026, 11, 6))))


class RoleTableTests(unittest.TestCase):
    def test_roles_without_tasks_are_hidden_with_explanation(self):
        o = O.analyze(source_fixture(), None, date(2026, 11, 6))
        html = V.tasks(o)
        self.assertIn("ЗамПред", [r["label"] for r in o["tasks"]["roles"]])   # в данных есть
        self.assertNotIn("ЗамПред", html)                                     # в таблице нет
        self.assertIn("МЗП", html)
        self.assertIn("В анализе участвовали все роли воронки", html)


class TaskQualityTests(unittest.TestCase):
    def test_reference_matching(self):
        from uzp_dash.dashboards.tb_health import reason_quality as R
        q = "1. Причина оттока\n{}\n2. Ожидаемый возврат получателей\nПункт не актуален"
        self.assertEqual(R.classify(q.format("Перенос даты выплаты ЗП (на декабрь)")), R.PROMISED)
        self.assertEqual(R.classify(q.format("Блокировка комплаенс\\Стоп-листы")), R.CONFIRMED)
        self.assertEqual(R.classify(q.format("Пункт не актуален"), "STAFF_TURNOVER"), R.PROMISED)
        self.assertEqual(R.classify(q.format("Сезонный бизнесмен")), "")
        self.assertEqual(R.classify(q.format("Недостаток контактных данных")), "")

    def test_quality_counts_tasks_against_fact_of_return(self):
        s = source_fixture()
        quest = {"t1": "1. Причина оттока\nПеренос даты выплаты ЗП",
                 "t2": "1. Причина оттока\nУшел в другой банк",
                 "t3": "1. Причина оттока\nПункт не актуален"}
        s.tasks["task_questionnaire"] = s.tasks.task_code.map(quest).fillna("")
        s.tasks.loc[s.tasks.task_code.eq("t3"), "task_comment"] = "COVENANTS"
        t = O.analyze(s, None, date(2026, 11, 6))["tasks"]
        q = t["quality"]
        self.assertEqual(q["classified"], 3)
        # t1: 101 за сен+окт — 120 ФЛ, вернулись 40
        self.assertEqual({k: q["promised"][k] for k in ("tasks", "flagged", "gone", "kept")},
                         {"tasks": 1, "flagged": 1, "gone": 120, "kept": 80})
        # t2 (101 за сен: 20/10) и t3 (102 за авг: 40/10) — возврат есть у обеих
        self.assertEqual({k: q["confirmed"][k] for k in ("tasks", "flagged", "gone", "returned")},
                         {"tasks": 2, "flagged": 2, "gone": 60, "returned": 20})
        html = V.tasks(O.analyze(s, None, date(2026, 11, 6)))
        self.assertIn("Качество отработки задач", html)
        self.assertIn("1 задача", html)
        self.assertIn("2 задачи", html)
        self.assertLess(html.index("Качество отработки"), html.index("Отработка задач по ролям"))


class SectionNarrativeTests(unittest.TestCase):
    """Выводы разделов: orgs не пересказывает отток, B2B не становится причиной."""

    def setUp(self):
        from uzp_dash import progress
        s = source_fixture()
        s.tasks["task_questionnaire"] = s.tasks.task_code.map(
            {"t1": "1. Причина оттока\nПеренос даты выплаты ЗП",
             "t3": "1. Причина оттока\nУшел в другой банк"}).fillna("")
        an = O.analyze(s, None, date(2026, 11, 6))
        an.update(total=61051, b2b=52541, returned=10309, return_target=26270.5,
                  return_gap=15961.5)
        row = dict(fact=100, plan=100, exec=1, rank=1, n_tb=9)
        self.a = SimpleNamespace(
            verdict={"rcp": row, "fot": row}, unit_label="ТБ", level="sb", gosb_cards=[],
            top_cells=pd.DataFrame(), to_work=pd.DataFrame(), pf={}, dates={}, closed={},
            tb_full="Сбербанк", ref_date="07.2026", activity={}, themes="—",
            no_point=pd.DataFrame(), outflow={"analytics": an},
            sim={"k": 475, "closable": 33432, "fot_mln": 2126, "attract": 27940,
                 "retention": 5492, "coverage": .9})
        self.patches = [patch.object(progress, "LOG_DIR", None),
                        patch.object(prompts, "_USED_OPENERS", {})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_llm(self, response):
        from unittest.mock import Mock
        llm = Mock(return_value=response)
        return prompts.section_narratives(SimpleNamespace(llm=llm), self.a), llm.call_args.args[0]

    def test_prompt_has_reasons_quality_and_block_labels(self):
        _, prompt = self.run_llm("")
        self.assertIn("Причины ухода B2B (зафиксированы", prompt)
        self.assertNotIn("Причина не зафиксирована —", prompt)
        self.assertIn("«обещали вернуть, но не вернули» — 1 задач", prompt)
        self.assertIn("B2B и B2C — тип оттока", prompt)
        self.assertIn("ОТОБРАННЫЙ СПИСОК (факты блока orgs)", prompt)
        self.assertIn("ОТТОК (факты блока outflow", prompt)

    def test_orgs_with_outflow_numbers_falls_back_and_b2b_reason_is_dropped(self):
        out, _ = self.run_llm(
            "[[orgs]]\nПриоритет — возврат: из 61 051 ушедшего подтверждён возврат "
            "только 10 309 чел B2B при цели 26 270.\n"
            "[[outflow]]\nОбещания вернуть не выполнены по 1 задаче. Главная причина "
            "ухода — B2B, 52 541 чел из 61 051. Долю B2B приводим с оговоркой: причина "
            "известна не по всем организациям. Основные потери в ТБ-11.")
        self.assertNotIn("61 051", out["orgs"])
        self.assertIn("475 организаций", out["orgs"])
        self.assertNotIn("Частые причины из комментариев: —", out["orgs"])
        self.assertNotIn("причина ухода — B2B", out["outflow"].lower().replace("главная ", ""))
        self.assertNotIn("Долю B2B", out["outflow"])
        self.assertIn("Обещания вернуть не выполнены", out["outflow"])

    def test_orgs_about_the_list_is_kept(self):
        text = ("Больший эффект в списке даёт привлечение: 27 940 из 33 432 чел. "
                "Начинать предлагается с ТБ с самым длинным списком.")
        out, _ = self.run_llm("[[orgs]]\n" + text)
        self.assertIn("27 940", out["orgs"])

    def test_no_known_reasons_is_said_explicitly(self):
        an = dict(self.a.outflow["analytics"], reasons=[], known_reason_gone=0)
        self.assertIn("не зафиксированы", prompts._outflow_analytics_detail(an))


class OutflowSourceTests(unittest.TestCase):
    def test_latest_source_never_moves_forecast_and_rolls_over_year_boundary(self):
        available = {date(2026, 9, 30), date(2026, 8, 31)}
        def read(engine, sql, params):
            if sql == Q.OUTFLOW_DATES:
                return pd.DataFrame({"report_dt": sorted(available)})
            return pd.DataFrame()
        dates = dict(ref_closed=date(2026, 10, 31), ref_cur=date(2026, 11, 30), ref_funnel=date(2026, 11, 30))
        with patch.object(O, "read_sql", side_effect=read):
            s = O.load(None, dates, set())
        self.assertEqual(s.dates["out_asof"], date(2026, 9, 30))
        self.assertEqual(dates["ref_cur"], date(2026, 11, 30))
        d = O.period_dates(date(2026, 1, 31), set())
        self.assertEqual(d["m_out3"], date(2025, 11, 30))
        self.assertEqual(d["m_prev1"], date(2025, 1, 31))

    def test_sql_join_does_not_multiply_facts_or_filter_b2c(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.create_function("btrim", 1, lambda s: s.strip() if s is not None else None)
        db.create_function("GREATEST", -1, max)
        db.create_function("LEAST", -1, min)
        db.executescript("""
          CREATE TABLE uzp_dim_gosb(old_gosb_id INT,new_gosb_id INT,tb_id INT,new_gosb_name TEXT,tb_short_name TEXT,region_name TEXT);
          INSERT INTO uzp_dim_gosb VALUES(1,10,18,'ГОСБ','ББ','Область А'),(1,10,18,'ГОСБ','ББ','Область А');
          CREATE TABLE uzp_dim_company(inn INT,company_name TEXT,holding_name TEXT);
          INSERT INTO uzp_dim_company VALUES(101,'Клиент','Х'),(101,'Клиент','Х');
          CREATE TABLE uzp_dwh_fact_outflow(report_dt TEXT,gosb_id INT,inn INT,segment_name TEXT,outflow_qty INT,is_force INT,mzp_fio TEXT);
          INSERT INTO uzp_dwh_fact_outflow VALUES('2026-10-31',1,101,'РГС',10,0,'Иванов'),('2026-10-31',1,102,'ММБ',2,0,NULL),('2026-11-30',1,101,'РГС',999,0,NULL);
          CREATE TABLE uzp_data_outflow_return_detail(report_dt TEXT,gosb_id INT,inn INT,return_qty INT);
          INSERT INTO uzp_data_outflow_return_detail VALUES('2026-10-31',1,101,8),('2026-10-31',1,101,7);
        """)
        dates = O.period_dates(date(2026, 10, 31), set())
        params = {k: v.isoformat() for k,v in dates.items() if isinstance(v,date)}
        rows = pd.read_sql_query(Q.OUTFLOW_OVERVIEW.format(schema="main"), db, params=params)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.gone.sum(), 12)
        self.assertEqual(rows.returned.sum(), 10)
        dates = pd.read_sql_query(Q.OUTFLOW_DATES.format(schema="main"), db,
                                 params={"ref_closed":"2026-10-31"})
        self.assertEqual(dates.report_dt.tolist(), ['2026-10-31'])


if __name__ == "__main__":
    unittest.main()
