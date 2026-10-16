"""Два слайда по сегменту РГС: снижение портфеля по ведомствам и до ИНН.

Отдельный файл, а не раздел дэша: это разовая выкладка на совещание, и жить
она должна своей жизнью — своя вёрстка под лист (16:9 на экране, A4 альбомный
на печати), никакой навигации и никакого LLM.

ЧТО СЧИТАЕТСЯ. Портфель получателей по организации на конец месяца —
`uzp_dwh_company_holding_metric.current_fl_qty`, строки `org_type='inn'`.
Уровень витрины берём `sb`: на нём организация лежит ОДНОЙ строкой, а на уровне
ГОСБ она разложена по подразделениям и в сумме по сети дала бы то же число, но
лишней арифметикой. Сверено на синтетике: sb, tb и gosb дают одинаковый итог.

ТРИ МЕСЯЦА, А НЕ ДВА. Чтобы показать снижение ЗА сентябрь и ЗА август, нужен и
июль: снижение за август — это август минус июль. Поэтому берутся три последних
закрытых месяца витрины, и подписаны они на слайде прямым текстом — отчёт не
должен молча считать не те месяцы, если витрина отстаёт.

ВЕДОМСТВО. Поля «ведомство» нет ни в одной витрине (см. uzp_dash/agency.py), оно
выводится правилами по наименованию организации. Поле `holding_name` из
`uzp_dim_company` показывается рядом там, где оно заполнено, — но группировка
идёт по ведомству, потому что в госсекторе холдинг заполнен далеко не у всех.

ФИЛЬТР ПО ТБ. Варианты обоих слайдов считаются и верстаются ЗАРАНЕЕ, по одному
на банк, а кнопка лишь показывает нужный. Так все правила счёта (склонения,
проценты, доли, хвост списка) остаются в одном месте — в Python, — и печать с
выгрузкой показывают ровно то же, что экран. Пересчёт в браузере означал бы
вторую копию тех же правил на JavaScript.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import agency, config, progress
from .db import get_engine, read_sql
from .render import components as C
from .render.xlsx import XLSX_JS
from .dashboards.tb_health import segments

# Сколько организаций печатаем на слайде раскрытия. Остальные — в выгрузке:
# на лист больше не влезает, а список из четырёхсот строк на совещании не читают.
TOP_N = 24

# Ключ варианта «вся сеть». Звёздочка, а не пустая строка: пустые значения
# data-атрибутов в разметке читаются хуже и теряются при копировании.
ALL = "*"

# Большие имена сегмента из справочника организаций, которые дают короткое «РГС».
# Берутся из общего маппинга сегментов, а не списком здесь: если на проме к
# сегменту добавят ещё одно большое имя, слайд подхватит его вместе с отчётом.
RGS_BIG = tuple(name for name, code in segments.BIG_TO_CODE.items()
                if segments.SHORT.get(code) == "РГС")

# Последние месяцы витрины. Уровень 'sb' — тот же, по которому считается портфель.
Q_MONTHS = """
SELECT DISTINCT report_dt
FROM {schema}.uzp_dwh_company_holding_metric
WHERE level_name = 'sb' AND org_type = 'inn'
ORDER BY report_dt DESC
LIMIT 12
"""

# Портфель организаций сегмента по трём месяцам.
#
# Даты перечислены поимённо, а не списком-параметром: IN :tuple требует
# expanding-параметра и ведёт себя по-разному в разных драйверах, а трёх
# именованных параметров достаточно и читается он однозначно.
Q_ORGS = """
SELECT m.org_id                        AS inn,
       m.report_dt                     AS report_dt,
       sum(COALESCE(m.current_fl_qty, 0)) AS fl
FROM {schema}.uzp_dwh_company_holding_metric m
JOIN {schema}.uzp_dim_company dc ON dc.inn = m.org_id
WHERE m.level_name = 'sb'
  AND m.org_type = 'inn'
  AND dc.segment_name IN :segs
  AND m.report_dt IN (CAST(:m0 AS date), CAST(:m1 AS date), CAST(:m2 AS date))
GROUP BY m.org_id, m.report_dt
"""

# Справка по организации: имя, холдинг. Отдельным запросом, чтобы не тащить
# текстовые поля через группировку по месяцам.
Q_COMPANY = """
SELECT dc.inn                  AS inn,
       min(dc.company_name)    AS company_name,
       min(dc.holding_name)    AS holding_name
FROM {schema}.uzp_dim_company dc
WHERE dc.segment_name IN :segs
GROUP BY dc.inn
"""

# Территориальный банк организации на последний месяц. Организация может
# обслуживаться в нескольких ТБ — берём тот, где её портфель больше.
Q_TB = """
SELECT m.org_id   AS inn,
       m.level_id AS tb_id,
       sum(COALESCE(m.current_fl_qty, 0)) AS fl
FROM {schema}.uzp_dwh_company_holding_metric m
JOIN {schema}.uzp_dim_company dc ON dc.inn = m.org_id
WHERE m.level_name = 'tb'
  AND m.org_type = 'inn'
  AND dc.segment_name IN :segs
  AND m.report_dt = CAST(:m0 AS date)
GROUP BY m.org_id, m.level_id
"""

Q_TB_NAMES = """
SELECT tb_id, min(tb_short_name) AS tb_short
FROM {schema}.uzp_dim_gosb
GROUP BY tb_id
"""


# --------------------------------------------------------------------------- #
def _months(engine, months: tuple | None) -> tuple[str, str, str]:
    """Три месяца разбора: последний, предыдущий и опорный для предыдущего.

    Передали явно — берём как есть (на проме выкладка может понадобиться за
    прошлый квартал). Не передали — три последних закрытых месяца витрины.
    """
    if months:
        if len(months) != 3:
            raise ValueError("months — ровно три даты: (сентябрь, август, июль)")
        return tuple(str(m) for m in months)      # type: ignore[return-value]
    df = read_sql(engine, Q_MONTHS)
    if len(df) < 3:
        raise RuntimeError("в витрине меньше трёх месяцев — сравнивать нечего")
    got = [str(pd.Timestamp(d).date()) for d in df["report_dt"].head(3)]
    return got[0], got[1], got[2]


def _label(dt: str, case: str = "nom") -> str:
    """Дата конца месяца → «сентябрь 2026»."""
    ts = pd.Timestamp(dt)
    return C.month_ru(f"{ts.month:02d}.{ts.year}", case)


def _load(engine, m0: str, m1: str, m2: str) -> pd.DataFrame:
    """Организации сегмента с портфелем по трём месяцам и признаками разбора."""
    segs = tuple(RGS_BIG)
    p = {"segs": segs, "m0": m0, "m1": m1, "m2": m2}
    raw = read_sql(engine, Q_ORGS, p)
    if raw.empty:
        raise RuntimeError(f"по сегменту РГС за {m0}, {m1}, {m2} в витрине пусто")
    raw["report_dt"] = [str(pd.Timestamp(x).date()) for x in raw["report_dt"]]
    wide = (raw.pivot_table(index="inn", columns="report_dt", values="fl",
                            aggfunc="sum")
               .reindex(columns=[m2, m1, m0]).fillna(0.0).reset_index())
    wide.columns = ["inn", "fl2", "fl1", "fl0"]

    comp = read_sql(engine, Q_COMPANY, {"segs": segs})
    wide = wide.merge(comp, on="inn", how="left")

    tb = read_sql(engine, Q_TB, p)
    if not tb.empty:
        names = read_sql(engine, Q_TB_NAMES)
        tb = (tb.sort_values("fl", ascending=False)
                .drop_duplicates("inn")
                .merge(names, on="tb_id", how="left"))
        wide = wide.merge(tb[["inn", "tb_short"]], on="inn", how="left")
    else:
        wide["tb_short"] = None
    wide["tb_short"] = wide["tb_short"].fillna("—")
    wide["holding_name"] = wide["holding_name"].fillna("")
    wide["company_name"] = wide["company_name"].fillna("—")

    # Ведомство — правилами по наименованию: поля в витрине нет.
    wide["agency"] = agency.classify_frame(wide, name_col="company_name")
    known, total, share = agency.coverage(wide["agency"])
    progress.done(f"Ведомство определено у {known} из {total} организаций РГС "
                  f"({share*100:.0f}%); остальные — «{agency.UNKNOWN}»")

    wide["d_last"] = wide["fl0"] - wide["fl1"]     # за последний месяц
    wide["d_prev"] = wide["fl1"] - wide["fl2"]     # за предыдущий
    wide["d_two"] = wide["fl0"] - wide["fl2"]      # за оба месяца
    return wide


def _by_agency(df: pd.DataFrame) -> pd.DataFrame:
    """Свод по ведомствам, худшее снижение за два месяца — сверху."""
    g = (df.groupby("agency", as_index=False)
           .agg(n=("inn", "nunique"), fl2=("fl2", "sum"), fl1=("fl1", "sum"),
                fl0=("fl0", "sum")))
    g["d_last"] = g["fl0"] - g["fl1"]
    g["d_prev"] = g["fl1"] - g["fl2"]
    g["d_two"] = g["fl0"] - g["fl2"]
    # Доля в снижении считается от СУММЫ СНИЖЕНИЙ, а не от итога сегмента: итог
    # может быть почти нулевым (одни ведомства падают, другие растут), и деление
    # на него дало бы доли в сотни процентов.
    drop = -g.loc[g["d_two"] < 0, "d_two"].sum()
    g["share"] = [(-d / drop if d < 0 and drop else 0.0) for d in g["d_two"]]
    return g.sort_values("d_two", kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------- #
def _pct(delta: float, base: float) -> str:
    """Изменение в процентах к базе месяца. Без базы процента нет.

    Ноль печатается без знака: «−0,0%» рядом с «0» читается как снижение,
    которого не было.
    """
    if not base:
        return "—"
    if abs(delta) < 0.5:
        return "0,0%"
    v = delta / base * 100
    sign = "+" if v > 0 else "−"
    return f"{sign}{abs(v):.1f}%".replace(".", ",")


def _num(delta: float) -> str:
    if abs(delta) < 0.5:
        return "0"
    return ("+" if delta > 0 else "−") + C.fmt_num(abs(delta))


def _cls(delta: float) -> str:
    return "up" if delta > 0.5 else ("down" if delta < -0.5 else "flat")


def _delta_cell(delta: float, base: float) -> str:
    """Ячейка изменения: число и процент одной колонкой, цвет по знаку."""
    return (f'<td class="num {_cls(delta)}"><b>{_num(delta)}</b>'
            f'<i>{_pct(delta, base)}</i></td>')


def _kpi(label: str, value: str, sub: str, cls: str = "") -> str:
    return (f'<div class="kpi {cls}"><div class="k-l">{C.esc(label)}</div>'
            f'<div class="k-v">{value}</div>'
            f'<div class="k-s">{sub}</div></div>')


def _scope(tb: str) -> str:
    """Подпись области разбора внутри варианта.

    Стоит в самом варианте, а не в шапке слайда: на печати и в PDF кнопок нет,
    и лист обязан сам говорить, по какому банку он посчитан.
    """
    txt = ("Вся сеть — все территориальные банки" if tb == ALL
           else f"Территориальный банк: {tb}")
    return f'<div class="scope">{C.esc(txt)}</div>'


# --------------------------------------------------------------------------- #
def _body_agencies(df: pd.DataFrame, g: pd.DataFrame, lbl: dict, tb: str) -> str:
    """Тело слайда 1 для одного варианта: плашки, вывод и таблица ведомств."""
    fl0, fl1, fl2 = df["fl0"].sum(), df["fl1"].sum(), df["fl2"].sum()
    d_last, d_prev, d_two = fl0 - fl1, fl1 - fl2, fl0 - fl2
    n_down = int((g["d_two"] < 0).sum())
    kpis = (
        _kpi(f'Портфель РГС на конец {lbl["m0_gen"]}', C.fmt_num(fl0),
             f'было {C.fmt_num(fl2)} на конец {lbl["m2_gen"]}')
        + _kpi(f'Снижение за {lbl["m0_acc"]}', _num(d_last),
               f'{_pct(d_last, fl1)} к {lbl["m1_dat"]}', _cls(d_last))
        + _kpi(f'Снижение за {lbl["m1_acc"]}', _num(d_prev),
               f'{_pct(d_prev, fl2)} к {lbl["m2_dat"]}', _cls(d_prev))
        + _kpi('Итого за два месяца', _num(d_two),
               f'{_pct(d_two, fl2)} · снижение в {n_down} '
               + C.plural(n_down, "ведомстве", "ведомствах", "ведомствах"),
               _cls(d_two))
    )
    # Вывод одной строкой: где сосредоточено снижение. Считается по тем же
    # числам, что в таблице, — на слайде не должно быть мысли, которую нельзя
    # проверить глазами по строке ниже.
    worst = g.iloc[0] if len(g) and g.iloc[0]["d_two"] < 0 else None
    if worst is not None:
        top_n = int((df[df["d_two"] < -0.5]["agency"] == worst["agency"]).sum())
        lead = (f'<div class="s-lead">Снижение сосредоточено в ведомстве '
                f'<b>{C.esc(worst["agency"])}</b>: '
                f'<b class="down">{_num(worst["d_two"])}</b> человек за два '
                f'месяца — это {worst["share"] * 100:.0f}% всего снижения '
                f'сегмента; падение идёт по {C.fmt_num(top_n)} '
                + C.plural(top_n, "организации", "организациям", "организациям")
                + ' этого ведомства.</div>')
    else:
        lead = ('<div class="s-lead">Снижения портфеля за эти два месяца '
                'в сегменте нет.</div>')
    rows = []
    for r in g.itertuples():
        bar = (f'<span class="bar"><i style="width:{min(r.share, 1) * 100:.0f}%">'
               f'</i></span><span class="bar-v">{r.share * 100:.0f}%</span>'
               if r.share > 0 else '<span class="bar-v flat">—</span>')
        rows.append(
            f'<tr><td class="name">{C.esc(r.agency)}'
            f'<i>{C.fmt_num(r.n)} '
            + C.plural(int(r.n), "организация", "организации", "организаций")
            + f'</i></td>'
            f'<td class="num dim">{C.fmt_num(r.fl2)}</td>'
            f'<td class="num dim">{C.fmt_num(r.fl1)}</td>'
            f'<td class="num">{C.fmt_num(r.fl0)}</td>'
            + _delta_cell(r.d_prev, r.fl2)
            + _delta_cell(r.d_last, r.fl1)
            + _delta_cell(r.d_two, r.fl2)
            + f'<td class="share">{bar}</td></tr>')
    total = (
        f'<tr class="tot"><td class="name">Итого РГС<i>{C.fmt_num(len(df))} '
        + C.plural(len(df), "организация", "организации", "организаций")
        + f'</i></td>'
        f'<td class="num dim">{C.fmt_num(fl2)}</td>'
        f'<td class="num dim">{C.fmt_num(fl1)}</td>'
        f'<td class="num">{C.fmt_num(fl0)}</td>'
        + _delta_cell(d_prev, fl2) + _delta_cell(d_last, fl1)
        + _delta_cell(d_two, fl2) + '<td class="share"></td></tr>')
    return f"""{_scope(tb)}
  <div class="kpis">{kpis}</div>
  {lead}
  <table class="t">
    <thead>
      <tr>
        <th>Ведомство</th>
        <th class="num">конец {C.esc(lbl["m2_gen"])}</th>
        <th class="num">конец {C.esc(lbl["m1_gen"])}</th>
        <th class="num">конец {C.esc(lbl["m0_gen"])}</th>
        <th class="num">за {C.esc(lbl["m1_acc"])}</th>
        <th class="num">за {C.esc(lbl["m0_acc"])}</th>
        <th class="num">за два месяца</th>
        <th class="share">доля в снижении</th>
      </tr>
    </thead>
    <tbody>{"".join(rows)}{total}</tbody>
  </table>"""


def _body_orgs(df: pd.DataFrame, lbl: dict, tb: str, has_holding: bool) -> str:
    """Тело слайда 2 для одного варианта: раскрытие до ИНН."""
    down = df[df["d_two"] < -0.5].sort_values("d_two", kind="stable")
    shown = down.head(TOP_N)
    rows = []
    for i, r in enumerate(shown.itertuples(), start=1):
        who = C.esc(str(r.company_name)[:44])
        sub = f'ИНН {C.esc(r.inn)} · {C.esc(r.agency)}'
        if tb == ALL:
            sub += f' · {C.esc(r.tb_short)}'
        if has_holding and str(r.holding_name).strip():
            sub += f' · {C.esc(str(r.holding_name)[:28])}'
        rows.append(
            f'<tr><td class="rk">{i}</td>'
            f'<td class="name">{who}<i>{sub}</i></td>'
            f'<td class="num dim">{C.fmt_num(r.fl2)}</td>'
            f'<td class="num">{C.fmt_num(r.fl0)}</td>'
            + _delta_cell(r.d_prev, r.fl2)
            + _delta_cell(r.d_last, r.fl1)
            + _delta_cell(r.d_two, r.fl2) + '</tr>')
    if not rows:
        rows.append('<tr><td class="rk"></td><td class="name" colspan="6">'
                    'Организаций со снижением портфеля за эти два месяца нет.'
                    '</td></tr>')
    tail_n = len(down) - len(shown)
    if tail_n > 0:
        tail_fl = down["d_two"].iloc[len(shown):].sum()
        rows.append(
            f'<tr class="tail"><td class="rk">…</td>'
            f'<td class="name">ещё {C.fmt_num(tail_n)} '
            + C.plural(tail_n, "организация", "организации", "организаций")
            + ' со снижением<i>полный список — в выгрузке</i></td>'
              '<td class="num dim"></td><td class="num dim"></td>'
              '<td class="num dim"></td><td class="num dim"></td>'
            + f'<td class="num down"><b>{_num(tail_fl)}</b><i></i></td></tr>')
    total_down = down["d_two"].sum()
    n_up = int((df["d_two"] > 0.5).sum())
    lead = (
        f'<div class="s-lead">За два месяца снизили портфель '
        f'<b>{C.fmt_num(len(down))}</b> '
        + C.plural(len(down), "организация", "организации", "организаций")
        + f' — суммарно <b class="down">{C.fmt_num(abs(total_down))}</b> '
        + C.plural(abs(total_down), "человек", "человека", "человек")
        + f'; выросли {C.fmt_num(n_up)}.'
        + (f' Ниже — первые {C.fmt_num(len(shown))} по размеру снижения.'
           if tail_n > 0 else '')
        + '</div>')
    return f"""{_scope(tb)}
  {lead}
  <table class="t t-org">
    <thead>
      <tr>
        <th class="rk">№</th>
        <th>Организация</th>
        <th class="num">конец {C.esc(lbl["m2_gen"])}</th>
        <th class="num">конец {C.esc(lbl["m0_gen"])}</th>
        <th class="num">за {C.esc(lbl["m1_acc"])}</th>
        <th class="num">за {C.esc(lbl["m0_acc"])}</th>
        <th class="num">за два месяца</th>
      </tr>
    </thead>
    <tbody>{"".join(rows)}</tbody>
  </table>"""


def _var(tb: str, body: str, first: bool) -> str:
    """Вариант слайда под один банк. Невидимые — атрибутом hidden: печать и
    выгрузка в PDF берут ровно то, что открыто на экране."""
    return (f'<div class="var" data-tb="{C.esc(tb)}"'
            + ('' if first else ' hidden') + f'>{body}</div>')


def _slide(eyebrow: str, title: str, meta: str, bodies: str, note: str) -> str:
    return f"""
<section class="slide">
  <header class="s-head">
    <div>
      <div class="eyebrow">{eyebrow}</div>
      <h1>{title}</h1>
    </div>
    <div class="s-meta">{meta}</div>
  </header>
  {bodies}
  <p class="note">{note}</p>
</section>"""


def _toolbar(tbs: list) -> str:
    """Фильтр по ТБ и выгрузка. Одна панель на обе страницы: фильтр общий,
    иначе слайды показывали бы разные банки и не сходились между собой."""
    chips = [f'<button type="button" class="tb-b on" data-tb="{ALL}">Вся сеть'
             f'</button>']
    chips += [f'<button type="button" class="tb-b" data-tb="{C.esc(t)}">'
              f'{C.esc(t)}</button>' for t in tbs]
    return (
        '<div class="bar">'
        '<span class="bar-l">Территориальный банк:</span>'
        f'<span class="tb-row">{"".join(chips)}</span>'
        '<button type="button" class="dl" onclick="rgsXlsx()">Выгрузить '
        'в Excel: оба слайда</button>'
        '</div>')


# --------------------------------------------------------------------------- #
_CSS = """
:root{
  --bg:#f5f5f7; --surface:#ffffff; --text:#1d1d1f; --text-2:#6e6e73;
  --separator:rgba(0,0,0,.09); --accent:#0071e3; --good:#34c759; --bad:#ff3b30;
  --radius:16px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--text);
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
       "Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased; padding:20px 16px 40px;
  font-variant-numeric:tabular-nums;
}
.deck{max-width:1320px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
/* --- панель фильтра: липкая, чтобы банк можно было сменить с любого слайда --- */
.bar{
  position:sticky; top:8px; z-index:5;
  display:flex; align-items:center; flex-wrap:wrap; gap:8px 10px;
  background:var(--surface); border:1px solid var(--separator);
  border-radius:999px; padding:9px 14px;
  box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 22px rgba(0,0,0,.06);
}
.bar-l{font-size:12.5px;color:var(--text-2)}
.tb-row{display:flex;flex-wrap:wrap;gap:6px}
.tb-b{
  font:inherit; font-size:12.5px; font-weight:600; cursor:pointer;
  color:var(--text-2); background:none; border:1px solid var(--separator);
  border-radius:999px; padding:4px 12px;
}
.tb-b:hover{color:var(--text);border-color:var(--text-2)}
.tb-b.on{color:#fff;background:var(--accent);border-color:var(--accent)}
.dl{
  margin-left:auto; font:inherit; font-size:12.5px; font-weight:600;
  cursor:pointer; color:var(--accent); background:none;
  border:1px solid var(--accent); border-radius:999px; padding:4px 13px;
}
.dl:hover{background:color-mix(in srgb,var(--accent) 8%,transparent)}
.slide{
  background:var(--surface); border:1px solid var(--separator);
  border-radius:var(--radius); padding:26px 28px 20px;
  box-shadow:0 1px 2px rgba(0,0,0,.04),0 10px 30px rgba(0,0,0,.05);
  break-inside:avoid;
}
.s-head{display:flex;justify-content:space-between;align-items:flex-start;
  gap:20px;border-bottom:1px solid var(--separator);padding-bottom:14px}
.eyebrow{font-size:12px;font-weight:700;letter-spacing:.04em;color:var(--accent)}
h1{margin:4px 0 0;font-size:27px;line-height:1.15;letter-spacing:-.02em;
  font-weight:700;text-wrap:balance}
.s-meta{text-align:right;font-size:13.5px;font-weight:650;white-space:nowrap}
.s-meta span{font-weight:400;color:var(--text-2);font-size:12.5px}
.scope{
  margin:12px 0 0; font-size:12px; font-weight:700; letter-spacing:.02em;
  color:var(--text-2);
}
.s-lead{margin:12px 0 4px;font-size:14.5px;color:var(--text)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:10px 0 6px}
.kpi{border:1px solid var(--separator);border-left:3px solid var(--separator);
  border-radius:12px;padding:10px 14px}
.kpi.down{border-left-color:var(--bad)} .kpi.up{border-left-color:var(--good)}
.k-l{font-size:12px;color:var(--text-2)}
.k-v{font-size:28px;font-weight:750;letter-spacing:-.025em;margin:1px 0}
.kpi.down .k-v{color:var(--bad)} .kpi.up .k-v{color:var(--good)}
.k-s{font-size:12px;color:var(--text-2)}
table.t{width:100%;border-collapse:collapse;margin-top:14px;font-size:13.5px}
table.t th{
  text-align:left;font-size:11.5px;font-weight:700;color:var(--text-2);
  letter-spacing:.01em;padding:0 8px 7px;border-bottom:1px solid var(--separator);
  vertical-align:bottom;
}
table.t th.num{text-align:right}
table.t td{padding:6px 8px;border-bottom:1px solid var(--separator)}
table.t tbody tr:last-child td{border-bottom:0}
td.name{font-weight:600;line-height:1.25}
td.name i{display:block;font-style:normal;font-weight:400;font-size:11.5px;
  color:var(--text-2);margin-top:1px}
td.num{text-align:right;white-space:nowrap}
td.num b{font-weight:700}
td.num i{display:block;font-style:normal;font-size:11.5px;font-weight:400;
  color:var(--text-2)}
td.dim{color:var(--text-2)}
td.down b,b.down{color:var(--bad)} td.up b{color:var(--good)}
td.flat b{color:var(--text-2)}
td.rk{width:26px;color:var(--text-2);font-size:12px;text-align:right}
th.rk{text-align:right}
tr.tot td{border-top:2px solid var(--text);border-bottom:0;font-weight:750;
  padding-top:8px}
tr.tot td.name i{font-weight:400}
tr.tail td{color:var(--text-2)}
th.share{text-align:left;width:140px}
td.share{white-space:nowrap}
.bar-wrap{display:inline-block}
.t .bar{
  position:static; display:inline-block; width:86px; height:7px; padding:0;
  border:0; border-radius:999px; box-shadow:none;
  background:color-mix(in srgb,var(--bad) 14%,transparent);
  vertical-align:middle; overflow:hidden;
}
.t .bar i{display:block;height:100%;background:var(--bad)}
.bar-v{font-size:12px;color:var(--text-2);margin-left:7px}
.bar-v.flat{margin-left:0}
.note{margin:12px 0 0;font-size:11.5px;line-height:1.45;color:var(--text-2)}
.foot{max-width:1320px;margin:0 auto;font-size:12px;color:var(--text-2)}
@media (max-width:900px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  h1{font-size:22px}
  table.t{font-size:12.5px}
  .bar{border-radius:14px}
  .dl{margin-left:0}
}
@page{size:A4 landscape;margin:10mm}
@media print{
  body{background:#fff;padding:0}
  .slide{box-shadow:none;border:0;border-radius:0;padding:0 0 10px;
    break-after:page}
  .deck{gap:0}
  .bar{display:none !important}
  .var[hidden]{display:none !important}
}
"""

_JS = """
<script>
/* Фильтр по ТБ: варианты слайдов уже свёрстаны, кнопка только показывает
   нужный. Выбор запоминается в переменной — его читает выгрузка, чтобы
   в Excel уехал тот же банк, что на экране. */
(function(){
  var cur = '*';
  function apply(tb){
    cur = tb;
    document.querySelectorAll('.var').forEach(function(v){
      v.hidden = (v.dataset.tb !== tb);
    });
    document.querySelectorAll('.tb-b').forEach(function(b){
      b.classList.toggle('on', b.dataset.tb === tb);
    });
    window.scrollTo({top: 0, behavior: 'instant'});
  }
  document.addEventListener('click', function(e){
    var b = e.target.closest ? e.target.closest('.tb-b') : null;
    if(b) apply(b.dataset.tb);
  });
  window.rgsTb = function(){ return cur; };
})();

/* Выгрузка обоих слайдов одной книгой: лист «Ведомства» и лист «Организации».
   В лист организаций уходят ВСЕ строки выбранного банка, а не показанные на
   слайде двадцать четыре. */
function rgsXlsx(){
  var d = window.__RGS || {}, tb = (window.rgsTb ? window.rgsTb() : '*');
  var v = (d.vars || {})[tb];
  if(!v || typeof window.xlsxBook !== 'function') return;
  var who = (tb === '*') ? 'вся сеть' : tb;
  window.xlsxBook('РГС ' + (d.period || '') + ' — ' + who, [
    {name: 'Ведомства', rows: [d.head_a].concat(v.a)},
    {name: 'Организации', rows: [d.head_o].concat(v.o)}
  ]);
}
</script>
"""


# --------------------------------------------------------------------------- #
def _export(variants: list, lbl: dict) -> str:
    """Данные выгрузки — по варианту на банк, теми же числами, что на слайде."""
    head_a = ["Ведомство", "Организаций",
              f'Портфель на конец {lbl["m2_gen"]}',
              f'Портфель на конец {lbl["m1_gen"]}',
              f'Портфель на конец {lbl["m0_gen"]}',
              f'Изменение за {lbl["m1_acc"]}', f'Изменение за {lbl["m1_acc"]}, %',
              f'Изменение за {lbl["m0_acc"]}', f'Изменение за {lbl["m0_acc"]}, %',
              "Изменение за два месяца", "Изменение за два месяца, %",
              "Доля в снижении, %"]
    head_o = ["Организация", "ИНН", "Ведомство", "Холдинг", "ТБ",
              f'Портфель на конец {lbl["m2_gen"]}',
              f'Портфель на конец {lbl["m1_gen"]}',
              f'Портфель на конец {lbl["m0_gen"]}',
              f'Изменение за {lbl["m1_acc"]}', f'Изменение за {lbl["m0_acc"]}',
              "Изменение за два месяца", "Изменение за два месяца, %"]

    def pct(d, b):
        return "" if not b else f"{d / b * 100:.1f}".replace(".", ",")

    out = {}
    for tb, sub, g in variants:
        rows_a = [[r.agency, int(r.n), int(r.fl2), int(r.fl1), int(r.fl0),
                   int(r.d_prev), pct(r.d_prev, r.fl2),
                   int(r.d_last), pct(r.d_last, r.fl1),
                   int(r.d_two), pct(r.d_two, r.fl2),
                   f"{r.share * 100:.0f}" if r.share > 0 else ""]
                  for r in g.itertuples()]
        fl2, fl1, fl0 = sub["fl2"].sum(), sub["fl1"].sum(), sub["fl0"].sum()
        rows_a.append(["Итого РГС", int(sub["inn"].nunique()),
                       int(fl2), int(fl1), int(fl0),
                       int(fl1 - fl2), pct(fl1 - fl2, fl2),
                       int(fl0 - fl1), pct(fl0 - fl1, fl1),
                       int(fl0 - fl2), pct(fl0 - fl2, fl2), ""])
        srt = sub.sort_values("d_two", kind="stable")
        rows_o = [[r.company_name, str(r.inn), r.agency, r.holding_name, r.tb_short,
                   int(r.fl2), int(r.fl1), int(r.fl0),
                   int(r.d_prev), int(r.d_last), int(r.d_two),
                   pct(r.d_two, r.fl2)]
                  for r in srt.itertuples()]
        out[tb] = {"a": rows_a, "o": rows_o}
    data = {"head_a": head_a, "head_o": head_o, "vars": out,
            "period": f'{lbl["m1_nom"]} — {lbl["m0_nom"]}'}
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script>window.__RGS={js};</script>'


def build(conn: str | None = None, months: tuple | None = None,
          out_dir: Path | None = None, verbose: bool = True) -> str:
    """Собрать слайды и положить рядом с отчётами. Возвращает путь к файлу."""
    progress.enable(verbose=verbose)
    engine = get_engine(config.db_url(conn))
    m0, m1, m2 = _months(engine, months)
    progress.step(f"РГС: месяцы разбора — {m2} → {m1} → {m0}")
    df = _load(engine, m0, m1, m2)
    lbl = {
        "m0_nom": _label(m0), "m1_nom": _label(m1), "m2_nom": _label(m2),
        "m0_gen": _label(m0, "gen"), "m1_gen": _label(m1, "gen"),
        "m2_gen": _label(m2, "gen"),
        "m1_dat": _label(m1, "dat"), "m2_dat": _label(m2, "dat"),
        # «за сентябрь» — винительный, у месяцев он совпадает с именительным
        "m0_acc": _label(m0).split()[0], "m1_acc": _label(m1).split()[0],
    }

    # Варианты: вся сеть и каждый банк, у которого в сегменте есть организации.
    tbs = sorted({t for t in df["tb_short"] if t and t != "—"})
    variants = [(ALL, df, _by_agency(df))]
    for t in tbs:
        sub = df[df["tb_short"] == t]
        variants.append((t, sub, _by_agency(sub)))
    has_holding = bool((df["holding_name"].astype(str).str.strip() != "").any())

    g_all = variants[0][2]
    progress.done(
        f"РГС: портфель {df['fl0'].sum():.0f} на конец {m0} · за {lbl['m0_acc']} "
        f"{df['d_last'].sum():+.0f}, за {lbl['m1_acc']} {df['d_prev'].sum():+.0f}, "
        f"за два месяца {df['d_two'].sum():+.0f} · ведомств со снижением "
        f"{int((g_all['d_two'] < 0).sum())} из {len(g_all)}")
    progress.done(f"Фильтр по ТБ: {len(tbs)} банков — "
                  + ", ".join(tbs))

    s1 = _slide(
        "Сегмент РГС · региональный госсектор",
        "Снижение портфеля по ведомствам",
        f'{C.esc(lbl["m1_nom"])} и {C.esc(lbl["m0_nom"])}<br>'
        '<span>портфель получателей на конец месяца</span>',
        "".join(_var(tb, _body_agencies(sub, g, lbl, tb), i == 0)
                for i, (tb, sub, g) in enumerate(variants)),
        "Снижение за месяц — портфель на конец этого месяца минус портфель на "
        "конец предыдущего. Проценты считаются к началу своего периода. Доля в "
        "снижении — часть суммарного снижения снижающихся ведомств, она "
        "складывается в 100%. Поля «ведомство» в витринах нет: оно определяется "
        "по наименованию организации, неразобранные имена стоят отдельной "
        "строкой и ни к какому ведомству не приписываются.")
    s2 = _slide(
        "Сегмент РГС · раскрытие до организации",
        "Где именно снизился портфель",
        f'{C.esc(lbl["m1_nom"])} и {C.esc(lbl["m0_nom"])}<br>'
        '<span>порядок — от наибольшего снижения</span>',
        "".join(_var(tb, _body_orgs(sub, lbl, tb, has_holding), i == 0)
                for i, (tb, sub, _g) in enumerate(variants)),
        "Портфель организации — получатели заработной платы на конец месяца. "
        "Организация, обслуживаемая в нескольких территориальных банках, "
        "отнесена к тому, где её портфель больше. Кнопка «Выгрузить в Excel» "
        "отдаёт книгу из двух листов — ведомства и организации — по выбранному "
        "банку, причём список организаций в ней полный, а не обрезанный по "
        "размеру листа.")

    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>РГС · снижение портфеля за {C.esc(lbl["m1_nom"])} и {C.esc(lbl["m0_nom"])}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="deck">
{_toolbar(tbs)}
{s1}
{s2}
</div>
<div class="foot">Источник: витрина uzp_dwh_company_holding_metric (портфель
получателей на конец месяца, строки по ИНН) и справочник организаций
uzp_dim_company. Отобран сегмент «{C.esc(", ".join(RGS_BIG))}» —
{C.fmt_num(len(df))} {C.plural(len(df), "организация", "организации", "организаций")}
в {C.fmt_num(len(tbs))} {C.plural(len(tbs), "банке", "банках", "банках")}.
Собрано {datetime.now():%d.%m.%Y, %H:%M}.</div>
{_export(variants, lbl)}
{XLSX_JS}
{_JS}
</body>
</html>"""

    d = Path(out_dir or config.OUTPUT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rgs_slides_{datetime.now():%Y%m%d_%H%M%S}.html"
    path.write_text(html, encoding="utf-8")
    progress.done(f"Готово: {path}")
    return str(path)


if __name__ == "__main__":                       # python -m uzp_dash.slides_rgs
    import sys
    args = sys.argv[1:]
    build(months=tuple(args[:3]) if len(args) >= 3 else None)
