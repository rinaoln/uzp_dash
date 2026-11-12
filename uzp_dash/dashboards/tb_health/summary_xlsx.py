"""Сводная выгрузка в Excel по всем ТБ и ГОСБ — в дополнение к HTML-отчёту.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Дэш отвечает на вопрос «что происходит и что делать», и
читают его глазами. Сводку же вставляют в общий шаблон банка «Портфель
ФЛ_Summary ТБ», где строки по всем подразделениям идут одним списком и дальше
живут в сводной таблице. Поэтому формат здесь чужой — не наш: колонки, их
порядок и формулировки повторяют шаблон, а не отчёт.

Одна строка = один показатель одного подразделения за один период:

    Показатель · За какой период · Уровень · ТБ · ГОСБ ·
    Какие у нас были ожидания · Что сейчас по факту ·
    Какие прогнозы на конец периода · Единицы измерения

Все три числа берутся из витрины за ОДИН И ТОТ ЖЕ месяц (прогнозный):
ожидания — план месяца, факт — сколько набрано на сегодня, прогноз — оценка
витрины на конец месяца. Смешивать сюда факт закрытого месяца нельзя: колонки
«ожидания» и «факт» тогда относились бы к разным периодам.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ... import progress
from ...render import components as C

RUB_TO_MLN = 1e6

# Заголовки — дословно из шаблона банка. Менять их нельзя: по ним в шаблоне
# настроены фильтры и сводные таблицы.
COLUMNS = [
    ("Показатель", 34),
    ("За какой период", 18),
    ("Уровень", 10),
    ("ТБ", 26),
    ("ГОСБ", 32),
    ("Какие у нас были ожидания (план, цель, ориентир и прочее)", 32),
    ("Что сейчас по факту", 20),
    ("Какие прогнозы на конец периода (если есть)", 24),
    ("Единицы измерения", 16),
]
# Синие колонки шапки — разрезы, оранжевые — сами числа (как в шаблоне)
BLUE = "2E75B6"
ORANGE = "FFC000"


def save(output_dir, b) -> Path | None:
    """Собрать и записать .xlsx рядом с отчётом. Вернуть путь или None."""
    rows = _rows(b)
    if not rows:
        progress.done("Сводная выгрузка не собрана: в витрине нет строк за "
                      "прогнозный месяц")
        return None
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        progress.warn("openpyxl не установлен — сводная выгрузка в Excel "
                      "не сформирована, отчёт собран как обычно")
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "Портфель ФЛ_Summary"
    ws.append([c[0] for c in COLUMNS])
    for i, (_, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
        cell = ws.cell(row=1, column=i)
        blue = i <= 5 or i == 9
        cell.fill = PatternFill("solid", fgColor=BLUE if blue else ORANGE)
        cell.font = Font(bold=True, color="FFFFFF" if blue else "000000")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    ws.row_dimensions[1].height = 46

    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2, min_col=6, max_col=8):
        for cell in row:
            # млн ₽ с десятыми, людей — целыми: единица стоит в колонке I
            unit = ws.cell(row=cell.row, column=9).value
            cell.number_format = "# ##0,0" if unit == "млн ₽" else "# ##0"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{ws.max_row}"
    ws.freeze_panes = "A2"

    d = Path(output_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"tb_health_summary_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    try:
        wb.save(path)
    except OSError as e:
        progress.warn(f"Сводная выгрузка не сохранена ({e}) — отчёт собран как обычно")
        return None
    progress.done(f"Сводная выгрузка по всем ТБ и ГОСБ: {path.name} — "
                  f"{len(rows)} строк в формате шаблона «Портфель ФЛ_Summary ТБ»")
    return path


def _rows(b) -> list[list]:
    """Строки выгрузки: подразделения в алфавитном порядке, показатели — подряд.

    Состав подразделений берётся из ТОЙ ЖЕ витрины, что и числа: подразделение
    попадает в файл тогда и только тогда, когда у него есть строка метрик за
    прогнозный месяц. Брать список из справочника нельзя — справочник шире
    витрины, и в файле появились бы подразделения с пустыми числами.

    Аппараты ТБ здесь ОСТАЮТСЯ, хотя в самом отчёте карточек у них нет: план
    сегмента нередко стоит именно на аппарате, и без него сумма ГОСБ разошлась бы
    с итогом ТБ на десятки процентов. В отчёте они не нужны (клиентской работы с
    аппаратом не ведут), в сводке — нужны.
    """
    d = b.dates or {}
    ref_cur = d.get("ref_cur")
    period = C.month_ru(d.get("label", ""))
    tb_full = {int(r.tb_id): str(r.tb_full_name) for r in b.tbs.itertuples()}
    tb_of = {int(k): int(v) for k, v in (b.tb_of or {}).items()}

    fl = _unit_metric(b.unit_tot, ref_cur)
    fot = _unit_metric(b.unit_tot_fot, ref_cur, scale=RUB_TO_MLN)
    deals = _deals(b, tb_of)

    # показатель -> (словарь значений, единица). Порядок задаёт порядок строк
    metrics = [("Портфель ФЛ", fl, "ФЛ"),
               ("ФОТ портфеля ФЛ", fot, "млн ₽"),
               ("Количество новых получателей", deals, "ФЛ")]

    units = _units(b.unit_tot, ref_cur, tb_full)
    out = []
    for level, key, tb_name, gosb_name in units:
        for title, src, unit in metrics:
            v = src.get(key)
            if not v or all(x is None for x in v):
                continue
            plan, fact, pred = v
            out.append([title, period, level, tb_name, gosb_name,
                        plan, fact, pred, unit])
    return out


def _units(df: pd.DataFrame, ref_cur, tb_full: dict) -> list[tuple]:
    """Подразделения файла: сначала ТБ, следом его ГОСБ — оба в алфавите.

    Подразделение читается блоком: строка ТБ и сразу под ней его ГОСБ. Собирать
    их по всему файлу фильтром пришлось бы при любой другой сортировке.
    """
    if df is None or df.empty:
        return []
    sub = df[df["end_dt"] == ref_cur]
    tbs, gosb = {}, {}
    for r in sub.itertuples():
        lvl, uid = str(r.level_name), int(r.unit_id)
        name = str(getattr(r, "unit_name", "") or "")
        if lvl == "tb":
            tbs[uid] = tb_full.get(uid, name)
        elif lvl == "gosb":
            gosb.setdefault(int(r.tb_id), []).append((uid, name))
    out = []
    for tb_id in sorted(tbs, key=lambda x: _ru(tbs[x])):
        out.append(("ТБ", ("tb", tb_id), tbs[tb_id], ""))
        for uid, name in sorted(gosb.get(tb_id, []), key=lambda x: _ru(x[1])):
            out.append(("ГОСБ", ("gosb", uid), tbs[tb_id], name))
    return out


def _ru(s: str) -> str:
    """Ключ сортировки: «ё» к «е», регистр не важен — как в самом отчёте."""
    return str(s or "").strip().lower().replace("ё", "е")


def _unit_metric(df: pd.DataFrame, ref_cur, scale: float = 1.0) -> dict:
    """{(уровень, id): (план, факт, прогноз)} за прогнозный месяц."""
    if df is None or df.empty:
        return {}
    sub = df[df["end_dt"] == ref_cur]
    out = {}
    for r in sub.itertuples():
        out[(str(r.level_name), int(r.unit_id))] = (
            _num(getattr(r, "plan_amt", None), scale),
            _num(getattr(r, "fact_amt", None), scale),
            _num(getattr(r, "pred_amt", None), scale),
        )
    return out


def _deals(b, tb_of: dict) -> dict:
    """Новые получатели по сделкам: план месяца, пришло на сегодня, прогноз.

    Считается по тем же строкам, по которым отчёт считал приход из воронки:
    план сделок месяца (`pipe_np_raw`), уже зачисленные (`pipe_fact_mtd`) и
    оценка на конец месяца с поправкой на историческую реализуемость (`pipe_np`).
    Свод до ГОСБ и до ТБ — сумма организаций: у сделок собственной строки в
    витрине метрик нет, и брать её неоткуда.
    """
    fc = getattr(b, "orgs_fc", None)
    if fc is None or fc.empty or "new_gosb_id" not in fc:
        return {}
    cols = [c for c in ("pipe_np_raw", "pipe_fact_mtd", "pipe_np") if c in fc]
    if len(cols) < 3:
        return {}
    g = fc.dropna(subset=["new_gosb_id"]).copy()
    g["new_gosb_id"] = g["new_gosb_id"].astype("int64")
    by_gosb = g.groupby("new_gosb_id")[cols].sum()
    out = {}
    for gid, r in by_gosb.iterrows():
        out[("gosb", int(gid))] = tuple(_num(r[c]) for c in cols)
    g["tb_id"] = g["new_gosb_id"].map(tb_of)
    by_tb = g.dropna(subset=["tb_id"]).groupby("tb_id")[cols].sum()
    for tb, r in by_tb.iterrows():
        out[("tb", int(tb))] = tuple(_num(r[c]) for c in cols)
    return out


def _num(v, scale: float = 1.0):
    """Число для ячейки: пусто вместо нуля-заглушки, чтобы не путать с фактом."""
    if v is None or pd.isna(v):
        return None
    v = float(v) / scale
    return round(v, 1) if scale != 1.0 else round(v)
