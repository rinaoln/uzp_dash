"""Два слайда по сегменту РГС: снижение портфеля в разрезах и до ИНН.

Отдельный дэш, а не раздел отчёта: это выкладка на совещание, и вёрстка у неё
своя — лист 16:9 на экране, A4 альбомный на печати, никакой навигации и никакого
LLM. Запускается как любой другой дэш: generate_dashboard("rgs_slides", …).

ЧТО СЧИТАЕТСЯ. Портфель получателей по организации на конец месяца —
`uzp_dwh_company_holding_metric.current_fl_qty`. Витрина лежит на грейне ГОСБ
(единственный уровень на проме, см. queries.py), поэтому строки сворачиваются до
пары «территориальный банк × организация»: так итог по сети равен сумме банков,
а организация, обслуживаемая в двух банках, попадает в каждый своей частью.

ТРИ МЕСЯЦА, А НЕ ДВА. Чтобы показать снижение ЗА сентябрь и ЗА август, нужен и
июль: снижение за август — это август минус июль. Берутся три последних месяца
витрины, подписаны они на слайде прямым текстом, а если между ними есть пропуск,
слайд говорит об этом отдельной строкой — отчёт не должен молча считать разницу
через дырку в данных.

РАЗРЕЗЫ. «Ведомство» выводится правилами по наименованию организации: поля для
него нет ни в одной витрине (см. uzp_dash/agency.py). «Холдинг» берётся из
`uzp_dim_company.holding_name` — на проме он заполнен примерно у трёх четвертей
организаций, остальные собираются в строку «Холдинг не указан». Оба разреза
считаются всегда, переключатель на слайде только показывает нужный.

ФИЛЬТР ПО ТБ. Варианты обоих слайдов считаются и верстаются ЗАРАНЕЕ, по одному
на банк, а кнопка лишь показывает нужный. Так все правила счёта (склонения,
проценты, доли, хвост списка) остаются в одном месте — в Python, — и печать с
выгрузкой показывают ровно то же, что экран.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from ... import agency, anonymize, config, progress
from ...db import get_engine, read_sql
from ...registry import Context, dashboard
from ...render import components as C
from ...render import veil
from ..tb_health import segments
from . import queries as Q

# Сколько организаций печатаем на слайде раскрытия. Остальные — в выгрузке:
# на лист больше не влезает, а список из сотен строк на совещании не читают.
TOP_N = 24
# Сколько строк показываем в разрезе. Ведомств всего одиннадцать, а холдингов на
# проме сотни: без потолка таблица уехала бы на три листа.
CUT_N = 14
# Потолок выгрузки на один банк. Книга лежит внутри html, и полный список
# организаций сегмента раздул бы файл до десятков мегабайт.
EXPORT_N = 1500

ALL = "*"                      # ключ варианта «вся сеть»
CUTS = (("ag", "Ведомства"), ("hd", "Холдинги"), ("in", "Отрасли"))
NO_HOLDING = "Холдинг не указан"
NO_INDUSTRY = "Отрасль не указана"

# Большие имена сегмента из справочника организаций, которые дают короткое «РГС».
# Берутся из общего маппинга сегментов, а не списком здесь: если на проме к
# сегменту добавят ещё одно большое имя, слайд подхватит его вместе с отчётом.
RGS_BIG = tuple(name for name, code in segments.BIG_TO_CODE.items()
                if segments.SHORT.get(code) == "РГС")

_ASSETS = Path(__file__).resolve().parent / "assets"


def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
def _ym(value: str) -> str:
    """«2026-09» или «2026-09-30» → дата КОНЦА месяца строкой.

    Месяц в витрине лежит последним днём, и помнить, что у сентября это 30-е, а
    у августа 31-е, читатель не обязан: в параметрах отчёта пишется месяц.
    """
    v = str(value).strip()
    if not v:
        return ""
    try:
        return str(pd.Period(v[:7], freq="M").to_timestamp("M").date())
    except Exception as ex:                   # noqa: BLE001 — это разбор ввода
        raise ValueError(f"месяц «{value}» не разобрался: ожидается «2026-09» "
                         f"или «2026-09-30»") from ex


def _months(engine, months: tuple | None = None, month: str = "",
            compare: str = "") -> tuple[str, str, str, str]:
    """Месяцы разбора: последний, предыдущий, опорный и месяц для сравнения.

    Первые три отвечают на вопрос «что происходит сейчас»: снижение за сентябрь
    — это сентябрь минус август, за август — август минус июль, поэтому опорный
    месяц нужен, хотя сам по себе не показывается. Четвёртый отвечает на другой
    вопрос: сезонная это просадка или портфель теряется весь год.

    Что можно задать:
      month   — отчётный месяц («2026-09»); пусто — последний в витрине;
      compare — с чем сравнивать год к году («2025-09»); пусто — тот же месяц
                год назад;
      months  — три-четыре точные даты конца месяца, если нужно обойти подбор
                целиком (например, сравнить квартал с кварталом).

    Предыдущие месяцы берутся не «минус один месяц», а ближайшие более ранние
    ИЗ ТЕХ, ЧТО ЕСТЬ в витрине: она грузится не каждый месяц, и вычитание из
    отсутствующего месяца дало бы снижение на весь портфель. Если месяцы вышли
    не подряд, слайд об этом говорит отдельной строкой.
    """
    have: list[str] = []
    try:
        df = read_sql(engine, Q.MONTHS, {"lvl": Q.LEVEL, "org": Q.ORG_TYPE})
        have = [str(pd.Timestamp(d).date()) for d in df["report_dt"]]
    except Exception as ex:                   # noqa: BLE001 — скажем об этом ниже
        progress.done(f"ВНИМАНИЕ: список месяцев витрины не прочитался "
                      f"({type(ex).__name__}: {ex})")
    if have:
        progress.done("Месяцы в витрине: "
                      + ", ".join(x[:7] for x in have[:14])
                      + (" …" if len(have) > 14 else ""))

    if months:                                # полный ручной режим
        if len(months) not in (3, 4):
            raise ValueError("months — три даты конца месяца "
                             "(сентябрь, август, июль) и необязательная "
                             "четвёртая: месяц для сравнения год к году")
        got = [_ym(m) for m in months[:3]]
        yoy = _ym(months[3]) if len(months) == 4 else ""
    else:
        if not have:
            hv = read_sql(engine, Q.LEVELS)
            what = " · ".join(
                f"{r.level_name}/{r.org_type}: {int(r.n)} строк, последний "
                f"{pd.Timestamp(r.last_dt).date()}" for r in hv.itertuples()
            ) or "таблица пуста"
            raise RuntimeError(
                f"в витрине uzp_dwh_company_holding_metric нет месяцев на "
                f"уровне {Q.LEVEL}/{Q.ORG_TYPE}. Что в ней есть: {what}")
        m0 = _ym(month) if month else have[0]
        if m0 not in have:
            raise RuntimeError(
                f"месяца {m0[:7]} в витрине нет. Доступны: "
                + ", ".join(x[:7] for x in have))
        # предыдущие — ближайшие более ранние из имеющихся, а не «минус месяц»
        earlier = [x for x in have if x < m0]
        if len(earlier) < 2:
            raise RuntimeError(
                f"до {m0[:7]} в витрине меньше двух месяцев — снижение за два "
                f"месяца посчитать не из чего. Доступны: "
                + ", ".join(x[:7] for x in have))
        got = [m0, earlier[0], earlier[1]]
        yoy = _ym(compare) if compare else ""

    if not yoy and not (months and len(months) == 4):
        # тот же месяц год назад: конец месяца, а не «минус 365 дней»
        yoy = str((pd.Timestamp(got[0]).to_period("M") - 12)
                  .to_timestamp("M").date())
    if yoy and have and yoy not in have:
        # список витрины мог не дотянуться — спрашиваем про месяц напрямую
        chk = read_sql(engine, Q.MONTH_EXISTS,
                       {"lvl": Q.LEVEL, "org": Q.ORG_TYPE, "m": yoy})
        if chk.empty or not int(chk.iloc[0]["n"]):
            progress.done(
                f"Месяца {yoy[:7]} в витрине нет — сравнение год к году на "
                f"слайдах не показывается. Доступны: "
                + ", ".join(x[:7] for x in have))
            yoy = ""
    progress.done(f"Месяцы разбора: {got[2]} → {got[1]} → {got[0]}"
                  + (f" · сравнение с {yoy}" if yoy else " · без года к году"))
    return got[0], got[1], got[2], yoy


def _gap_note(m0: str, m1: str, m2: str) -> str:
    """Предупреждение, если взятые месяцы идут не подряд.

    Витрина грузится не каждый месяц (в профиле видны пропуски), и «снижение за
    август» между июнем и сентябрём было бы неправдой. Молчать нельзя: числа
    посчитаются, и ошибку никто не заметит.
    """
    # Сравниваем ПОРЯДКОВЫЕ номера месяцев: разность двух Period в pandas —
    # это смещение (<MonthEnd>), а не число, и проверка «== 1» на нём всегда
    # ложна — оговорка про пропуск вылезала бы на подряд идущих месяцах.
    got = [pd.Timestamp(x).to_period("M").ordinal for x in (m2, m1, m0)]
    if got[1] - got[0] == 1 and got[2] - got[1] == 1:
        return ""
    progress.done("ВНИМАНИЕ: месяцы витрины идут не подряд — на слайде стоит "
                  "оговорка, что это разница между доступными месяцами")
    return ('<p class="warn">В витрине есть не все месяцы подряд: показана '
            'разница между теми, что загружены, а не строго между соседними '
            'месяцами.</p>')


def _segments_present(engine) -> str:
    """Какие сегменты есть в справочнике — для сообщения об ошибке."""
    try:
        df = read_sql(engine, Q.SEGMENTS)
    except Exception:                       # noqa: BLE001 — это путь ошибки
        return "прочитать справочник не удалось"
    return " · ".join(f"{r.segment_name} ({int(r.n)})"
                      for r in df.head(12).itertuples())


def _load(engine, m0: str, m1: str, m2: str, my: str = "") -> pd.DataFrame:
    """Портфель сегмента по месяцам разбора на грейне (ТБ, организация).

    Четвёртый месяц (год назад) может отсутствовать — тогда в отбор уходит
    повтор опорного месяца, колонка прошлого года остаётся нулевой и сравнение
    год к году нигде не показывается.
    """
    segs = tuple(RGS_BIG)
    p = {"segs": segs, "m0": m0, "m1": m1, "m2": m2, "my": my or m2,
         "lvl": Q.LEVEL, "org": Q.ORG_TYPE}
    raw = read_sql(engine, Q.ORGS, p)
    if raw.empty:
        raise RuntimeError(
            f"по сегменту {', '.join(RGS_BIG)} за {m2}, {m1}, {m0} в витрине "
            f"пусто (уровень {Q.LEVEL}/{Q.ORG_TYPE}). Сегменты в справочнике "
            f"организаций: {_segments_present(engine)}")
    raw["report_dt"] = [str(pd.Timestamp(x).date()) for x in raw["report_dt"]]
    cols = [m2, m1, m0] + ([my] if my and my not in (m2, m1, m0) else [])
    wide = (raw.pivot_table(index=["tb_short", "inn"], columns="report_dt",
                            values="fl", aggfunc="sum")
               .reindex(columns=cols).fillna(0.0).reset_index())
    wide.columns = (["tb_short", "inn", "fl2", "fl1", "fl0"]
                    + (["fly"] if len(cols) == 4 else []))
    if "fly" not in wide:
        wide["fly"] = 0.0

    comp = read_sql(engine, Q.COMPANY, {"segs": segs})
    wide = wide.merge(comp, on="inn", how="left")
    wide = wide.merge(_industry(engine, segs), on="inn", how="left")
    wide["holding_name"] = (wide["holding_name"].fillna("").astype(str)
                            .str.strip())
    if "industry_name" not in wide:
        wide["industry_name"] = ""
    wide["industry_name"] = (wide["industry_name"].fillna("").astype(str)
                             .str.strip())
    wide["company_name"] = wide["company_name"].fillna("")
    # Имя человека вместо названия организации (ИП, КФХ) в отчёт не идёт —
    # то же правило, что и в основном отчёте, и живёт оно в одном месте
    # Сегмент тут один — РГС, строгое правило ММБ к нему не применяется
    wide, n_person, _ = anonymize.mask_people(wide)
    if n_person:
        progress.done(f"Названия-имена людей заменены номером организации: "
                      f"{n_person} из {len(wide)} строк")
    wide["company_name"] = wide["company_name"].replace("", "—")

    # Ведомство — правилами по наименованию: поля в витрине нет. Отрасль из
    # витрины ЕПК идёт подсказкой: она срабатывает только там, где наименование
    # не разобралось, и не перебивает разбор по вывеске (см. agency.classify).
    wide["agency"] = agency.classify_frame(wide, name_col="company_name",
                                           industry_col="industry_name")
    known, total, share = agency.coverage(wide["agency"])
    progress.done(f"Ведомство определено у {known} из {total} строк "
                  f"({share*100:.0f}%); остальные — «{agency.UNKNOWN}»")
    n_hold = int((wide["holding_name"] != "").sum())
    progress.done(f"Холдинг заполнен у {n_hold} из {total} строк "
                  f"({n_hold / total * 100:.0f}%)")
    n_ind = int((wide["industry_name"] != "").sum())
    progress.done(f"Отрасль заполнена у {n_ind} из {total} строк "
                  f"({n_ind / total * 100:.0f}%)")
    no_tb = int((wide["tb_short"] == "—").sum())
    if no_tb:
        progress.done(f"ВНИМАНИЕ: у {no_tb} строк ГОСБ не нашёлся в справочнике "
                      f"подразделений — они собраны в банк «—», но из итога по "
                      f"сегменту не выпали")

    wide["holding"] = [h or NO_HOLDING for h in wide["holding_name"]]
    wide["industry"] = [i or NO_INDUSTRY for i in wide["industry_name"]]
    wide["d_last"] = wide["fl0"] - wide["fl1"]     # за последний месяц
    wide["d_prev"] = wide["fl1"] - wide["fl2"]     # за предыдущий
    wide["d_two"] = wide["fl0"] - wide["fl2"]      # за оба месяца
    wide["d_yoy"] = wide["fl0"] - wide["fly"]      # год к году
    return wide


def _industry(engine, segs: tuple) -> pd.DataFrame:
    """Отрасль организации из витрины атрибутов ЕПК: {inn, industry_name}.

    Сначала точная связь по epk_id (ключ витрины), затем запасная по ИНН — ею
    добираются организации, чей epk_id в витрине не нашёлся. Заполняемость
    печатается в лог обоими числами: на проме поле заполнено примерно у
    десятой части организаций, и по какому именно сегменту как — видно только
    в прогоне.

    Сбой запроса не роняет сборку: отрасль — дополнительный разрез, и слайды
    без неё остаются осмысленными. Но молчать о сбое нельзя, иначе пустой
    разрез выглядел бы как «у всех отрасль не указана».
    """
    empty = pd.DataFrame({"inn": [], "industry_name": []})
    try:
        by_epk = read_sql(engine, Q.INDUSTRY_BY_EPK, {"segs": segs})
        by_inn = read_sql(engine, Q.INDUSTRY_BY_INN, {"segs": segs})
    except Exception as ex:                   # noqa: BLE001 — разрез необязателен
        progress.done(f"ВНИМАНИЕ: отрасль из uzp_data_epk_consolidation прочитать "
                      f"не удалось ({type(ex).__name__}: {ex}) — разрез «Отрасли» "
                      f"будет пустым, остальные слайды не пострадали")
        return empty
    out = by_epk.rename(columns={"industry_name": "ind_epk"})
    out = out.merge(by_inn.rename(columns={"industry_name": "ind_inn"}),
                    on="inn", how="outer")
    if out.empty:
        progress.done("Отрасль из uzp_data_epk_consolidation: не нашлось ни одной "
                      "записи по сегменту — разрез «Отрасли» будет пустым")
        return empty
    out["industry_name"] = out["ind_epk"].fillna(out["ind_inn"])
    n_epk = int(out["ind_epk"].notna().sum())
    n_add = int((out["ind_epk"].isna() & out["ind_inn"].notna()).sum())
    progress.done(f"Отрасль из uzp_data_epk_consolidation: {n_epk} организаций по "
                  f"epk_id, ещё {n_add} по ИНН · разных отраслей "
                  f"{out['industry_name'].nunique()}")
    return out[["inn", "industry_name"]]


def _cut(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Свод по разрезу (ведомство или холдинг), худшее снижение — сверху."""
    g = (df.groupby(col, as_index=False)
           .agg(n=("inn", "nunique"), fl2=("fl2", "sum"), fl1=("fl1", "sum"),
                fl0=("fl0", "sum"), fly=("fly", "sum")))
    g = g.rename(columns={col: "name"})
    g["d_last"] = g["fl0"] - g["fl1"]
    g["d_prev"] = g["fl1"] - g["fl2"]
    g["d_two"] = g["fl0"] - g["fl2"]
    g["d_yoy"] = g["fl0"] - g["fly"]
    # Доля в снижении считается от СУММЫ СНИЖЕНИЙ, а не от итога сегмента: итог
    # может быть почти нулевым (одни строки падают, другие растут), и деление
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
def _body_cut(df: pd.DataFrame, g: pd.DataFrame, lbl: dict, tb: str,
              cut: str) -> str:
    """Тело слайда 1 для одного варианта: плашки, вывод и таблица разреза."""
    fl0, fl1, fl2 = df["fl0"].sum(), df["fl1"].sum(), df["fl2"].sum()
    d_last, d_prev, d_two = fl0 - fl1, fl1 - fl2, fl0 - fl2
    n_down = int((g["d_two"] < 0).sum())
    what = {"ag": "ведомстве", "hd": "холдинге", "in": "отрасли"}[cut]
    whats = {"ag": "ведомствах", "hd": "холдингах", "in": "отраслях"}[cut]
    kpis = (
        _kpi(f'Портфель РГС на конец {lbl["m0_gen"]}', C.fmt_num(fl0),
             f'было {C.fmt_num(fl2)} на конец {lbl["m2_gen"]}')
        + _kpi(f'Снижение за {lbl["m0_acc"]}', _num(d_last),
               f'{_pct(d_last, fl1)} к {lbl["m1_dat"]}', _cls(d_last))
        + _kpi(f'Снижение за {lbl["m1_acc"]}', _num(d_prev),
               f'{_pct(d_prev, fl2)} к {lbl["m2_dat"]}', _cls(d_prev))
        + _kpi('Итого за два месяца', _num(d_two),
               f'{_pct(d_two, fl2)} · снижение в {n_down} '
               + C.plural(n_down, what, whats, whats), _cls(d_two))
    )
    # Пятая плашка — другой вопрос: не «что было в эти два месяца», а
    # «сезонная это просадка или портфель теряется». Без неё снижение за два
    # месяца нечем взвесить.
    yoy = "my_nom" in lbl
    if yoy:
        fly = df["fly"].sum()
        d_yoy = fl0 - fly
        kpis += _kpi(f'Год к году, к {lbl["my_dat"]}', _num(d_yoy),
                     f'{_pct(d_yoy, fly)} · было {C.fmt_num(fly)}', _cls(d_yoy))
    # Вывод одной строкой: где сосредоточено снижение. Считается по тем же
    # числам, что в таблице, — на слайде не должно быть мысли, которую нельзя
    # проверить глазами по строке ниже.
    worst = g.iloc[0] if len(g) and g.iloc[0]["d_two"] < 0 else None
    if worst is not None:
        col = {"ag": "agency", "hd": "holding", "in": "industry"}[cut]
        top_n = int((df[df["d_two"] < -0.5][col] == worst["name"]).sum())
        lead = (f'<div class="s-lead">Снижение сосредоточено в {what} '
                f'<b>{C.esc(worst["name"])}</b>: '
                f'<b class="down">{_num(worst["d_two"])}</b> человек за два '
                f'месяца — это {worst["share"] * 100:.0f}% всего снижения '
                f'сегмента; падение идёт по {C.fmt_num(top_n)} '
                + C.plural(top_n, "организации", "организациям", "организациям")
                + '.</div>')
    else:
        lead = ('<div class="s-lead">Снижения портфеля за эти два месяца '
                'в сегменте нет.</div>')

    shown, rest = g.head(CUT_N), g.iloc[CUT_N:]
    rows = []
    for r in shown.itertuples():
        bar = (f'<span class="bar"><i style="width:{min(r.share, 1) * 100:.0f}%">'
               f'</i></span><span class="bar-v">{r.share * 100:.0f}%</span>'
               if r.share > 0 else '<span class="bar-v flat">—</span>')
        rows.append(
            f'<tr><td class="name">{C.esc(str(r.name)[:46])}'
            f'<i>{C.fmt_num(r.n)} '
            + C.plural(int(r.n), "организация", "организации", "организаций")
            + '</i></td>'
            f'<td class="num dim">{C.fmt_num(r.fl2)}</td>'
            f'<td class="num dim">{C.fmt_num(r.fl1)}</td>'
            f'<td class="num">{C.fmt_num(r.fl0)}</td>'
            + _delta_cell(r.d_prev, r.fl2)
            + _delta_cell(r.d_last, r.fl1)
            + _delta_cell(r.d_two, r.fl2)
            + (_delta_cell(r.d_yoy, r.fly) if yoy else "")
            + f'<td class="share">{bar}</td></tr>')
    if len(rest):
        many = {"ag": ("ведомства", "ведомств"), "hd": ("холдинга", "холдингов"),
                "in": ("отрасли", "отраслей")}[cut]
        rows.append(
            f'<tr class="tail"><td class="name">ещё {C.fmt_num(len(rest))} '
            + C.plural(len(rest), many[0], many[0], many[1])
            + f'<i>{C.fmt_num(rest["n"].sum())} организаций</i></td>'
            f'<td class="num dim">{C.fmt_num(rest["fl2"].sum())}</td>'
            f'<td class="num dim">{C.fmt_num(rest["fl1"].sum())}</td>'
            f'<td class="num">{C.fmt_num(rest["fl0"].sum())}</td>'
            + _delta_cell(rest["d_prev"].sum(), rest["fl2"].sum())
            + _delta_cell(rest["d_last"].sum(), rest["fl1"].sum())
            + _delta_cell(rest["d_two"].sum(), rest["fl2"].sum())
            + (_delta_cell(rest["d_yoy"].sum(), rest["fly"].sum())
               if yoy else "")
            + f'<td class="share"><span class="bar-v">'
              f'{rest["share"].sum() * 100:.0f}%</span></td></tr>')
    total = (
        f'<tr class="tot"><td class="name">Итого РГС'
        f'<i>{C.fmt_num(df["inn"].nunique())} '
        + C.plural(int(df["inn"].nunique()), "организация", "организации",
                   "организаций")
        + '</i></td>'
        f'<td class="num dim">{C.fmt_num(fl2)}</td>'
        f'<td class="num dim">{C.fmt_num(fl1)}</td>'
        f'<td class="num">{C.fmt_num(fl0)}</td>'
        + _delta_cell(d_prev, fl2) + _delta_cell(d_last, fl1)
        + _delta_cell(d_two, fl2)
        + (_delta_cell(fl0 - df["fly"].sum(), df["fly"].sum()) if yoy else "")
        + '<td class="share"></td></tr>')
    head = {"ag": "Ведомство", "hd": "Холдинг", "in": "Отрасль"}[cut]
    yoy_th = (f'<th class="num">год к году<br>к {C.esc(lbl["my_dat"])}</th>'
              if yoy else "")
    return f"""{_scope(tb)}
  <div class="kpis">{kpis}</div>
  {lead}
  <table class="t">
    <thead>
      <tr>
        <th>{head}</th>
        <th class="num">конец {C.esc(lbl["m2_gen"])}</th>
        <th class="num">конец {C.esc(lbl["m1_gen"])}</th>
        <th class="num">конец {C.esc(lbl["m0_gen"])}</th>
        <th class="num">за {C.esc(lbl["m1_acc"])}</th>
        <th class="num">за {C.esc(lbl["m0_acc"])}</th>
        <th class="num">за два месяца</th>
        {yoy_th}
        <th class="share">доля в снижении</th>
      </tr>
    </thead>
    <tbody>{"".join(rows)}{total}</tbody>
  </table>"""


def _body_orgs(df: pd.DataFrame, lbl: dict, tb: str, has_holding: bool) -> str:
    """Тело слайда 2 для одного варианта: раскрытие до ИНН.

    Порядок — по снижению за два месяца: слайд отвечает на вопрос «что
    происходит сейчас». Колонка года к году стоит рядом, чтобы по той же
    строке было видно, разовая это просадка или организация теряет портфель
    весь год.
    """
    yoy = "my_nom" in lbl
    down = df[df["d_two"] < -0.5].sort_values("d_two", kind="stable")
    shown = down.head(TOP_N)
    rows = []
    for i, r in enumerate(shown.itertuples(), start=1):
        who = C.esc(str(r.company_name)[:44])
        sub = f'ИНН {C.esc(r.inn)} · {C.esc(r.agency)}'
        if tb == ALL:
            sub += f' · {C.esc(r.tb_short)}'
        if has_holding and r.holding_name:
            sub += f' · {C.esc(str(r.holding_name)[:28])}'
        rows.append(
            f'<tr><td class="rk">{i}</td>'
            f'<td class="name">{who}<i>{sub}</i></td>'
            f'<td class="num dim">{C.fmt_num(r.fl2)}</td>'
            f'<td class="num">{C.fmt_num(r.fl0)}</td>'
            + _delta_cell(r.d_prev, r.fl2)
            + _delta_cell(r.d_last, r.fl1)
            + _delta_cell(r.d_two, r.fl2)
            + (_delta_cell(r.d_yoy, r.fly) if yoy else "") + '</tr>')
    if not rows:
        rows.append('<tr><td class="rk"></td><td class="name" colspan="7">'
                    'Организаций со снижением портфеля за эти два месяца нет.'
                    '</td></tr>')
    tail_n = len(down) - len(shown)
    if tail_n > 0:
        tail_fl = down["d_two"].iloc[len(shown):].sum()
        tail_yoy = down["d_yoy"].iloc[len(shown):].sum()
        rows.append(
            f'<tr class="tail"><td class="rk">…</td>'
            f'<td class="name">ещё {C.fmt_num(tail_n)} '
            + C.plural(tail_n, "организация", "организации", "организаций")
            + ' со снижением<i>полный список — в выгрузке</i></td>'
              '<td class="num dim"></td><td class="num dim"></td>'
              '<td class="num dim"></td><td class="num dim"></td>'
            + f'<td class="num down"><b>{_num(tail_fl)}</b><i></i></td>'
            + (f'<td class="num {_cls(tail_yoy)}"><b>{_num(tail_yoy)}</b>'
               f'<i></i></td>' if yoy else "") + '</tr>')
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
        {f'<th class="num">год к году<br>к {C.esc(lbl["my_dat"])}</th>'
         if yoy else ""}
      </tr>
    </thead>
    <tbody>{"".join(rows)}</tbody>
  </table>"""


def _var(body: str, tb: str, first: bool, cut: str = "") -> str:
    """Вариант слайда. Невидимые — атрибутом hidden: печать и выгрузка в PDF
    берут ровно то, что открыто на экране."""
    attr = f' data-tb="{C.esc(tb)}"' + (f' data-cut="{cut}"' if cut else "")
    return (f'<div class="var"{attr}'
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
    """Фильтр по ТБ, переключатель разреза и выгрузка. Одна панель на оба
    слайда: фильтр общий, иначе слайды показывали бы разные банки."""
    chips = [f'<button type="button" class="tb-b on" data-tb="{ALL}">Вся сеть'
             f'</button>']
    chips += [f'<button type="button" class="tb-b" data-tb="{C.esc(t)}">'
              f'{C.esc(t)}</button>' for t in tbs]
    cuts = "".join(
        f'<button type="button" class="cut-b{" on" if i == 0 else ""}" '
        f'data-cut="{k}">{C.esc(name)}</button>'
        for i, (k, name) in enumerate(CUTS))
    return (
        '<div class="bar">'
        '<span class="bar-l">Территориальный банк:</span>'
        f'<span class="tb-row">{"".join(chips)}</span>'
        '<span class="bar-l">Разрез:</span>'
        f'<span class="tb-row">{cuts}</span>'
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
  border-radius:16px; padding:9px 14px;
  box-shadow:0 1px 2px rgba(0,0,0,.04),0 8px 22px rgba(0,0,0,.06);
}
.bar-l{font-size:12.5px;color:var(--text-2)}
.tb-row{display:flex;flex-wrap:wrap;gap:6px}
.tb-b,.cut-b{
  font:inherit; font-size:12.5px; font-weight:600; cursor:pointer;
  color:var(--text-2); background:none; border:1px solid var(--separator);
  border-radius:999px; padding:4px 12px;
}
.tb-b:hover,.cut-b:hover{color:var(--text);border-color:var(--text-2)}
.tb-b.on{color:#fff;background:var(--accent);border-color:var(--accent)}
.cut-b.on{color:var(--accent);border-color:var(--accent)}
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
.warn{
  margin:12px 0 0; padding:8px 12px; font-size:12.5px;
  border-left:3px solid var(--bad); background:color-mix(in srgb,var(--bad) 6%,transparent);
  border-radius:0 8px 8px 0;
}
.kpis{display:grid;gap:12px;margin:10px 0 6px;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr))}
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
tr.tail td,tr.tail td.name{color:var(--text-2);font-weight:400}
th.share{text-align:left;width:140px}
td.share{white-space:nowrap}
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
  h1{font-size:22px}
  table.t{font-size:12.5px}
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
/* Фильтр по ТБ и разрезу: варианты слайдов уже свёрстаны, кнопки только
   показывают нужный. Выбор запоминается — его читает выгрузка, чтобы в Excel
   уехал тот же банк, что на экране. */
(function(){
  var tb = '*', cut = 'ag';
  function apply(){
    document.querySelectorAll('.var').forEach(function(v){
      var okTb = (v.dataset.tb === tb);
      var okCut = (!v.dataset.cut || v.dataset.cut === cut);
      v.hidden = !(okTb && okCut);
    });
    document.querySelectorAll('.tb-b').forEach(function(b){
      b.classList.toggle('on', b.dataset.tb === tb);
    });
    document.querySelectorAll('.cut-b').forEach(function(b){
      b.classList.toggle('on', b.dataset.cut === cut);
    });
  }
  document.addEventListener('click', function(e){
    if(!e.target.closest) return;
    var b = e.target.closest('.tb-b');
    if(b){ tb = b.dataset.tb; apply(); window.scrollTo({top:0, behavior:'instant'}); return; }
    var c = e.target.closest('.cut-b');
    if(c){ cut = c.dataset.cut; apply(); }
  });
  window.rgsTb = function(){ return tb; };
})();

/* Выгрузка обоих слайдов одной книгой: оба разреза и организации выбранного
   банка. В лист организаций уходят все строки со снижением, а не показанные
   на слайде двадцать четыре. */
function rgsXlsx(){
  var d = window.__RGS || {}, tb = (window.rgsTb ? window.rgsTb() : '*');
  var v = (d.vars || {})[tb];
  if(!v || typeof window.xlsxBook !== 'function') return;
  var who = (tb === '*') ? 'вся сеть' : tb;
  window.xlsxBook('РГС ' + (d.period || '') + ' — ' + who, [
    {name: 'Ведомства', rows: [d.head_c].concat(v.ag)},
    {name: 'Холдинги', rows: [d.head_h].concat(v.hd)},
    {name: 'Отрасли', rows: [d.head_i].concat(v['in'])},
    {name: 'Организации', rows: [d.head_o].concat(v.o)}
  ]);
}
</script>
"""


# --------------------------------------------------------------------------- #
def _export(variants: list, lbl: dict) -> str:
    """Данные выгрузки — по варианту на банк, теми же числами, что на слайде."""
    yoy = "my_nom" in lbl

    def cut_head(first: str) -> list:
        return [first, "Организаций",
                f'Портфель на конец {lbl["m2_gen"]}',
                f'Портфель на конец {lbl["m1_gen"]}',
                f'Портфель на конец {lbl["m0_gen"]}',
                f'Изменение за {lbl["m1_acc"]}',
                f'Изменение за {lbl["m1_acc"]}, %',
                f'Изменение за {lbl["m0_acc"]}',
                f'Изменение за {lbl["m0_acc"]}, %',
                "Изменение за два месяца", "Изменение за два месяца, %",
                "Доля в снижении, %"] + ([
                f'Портфель на конец {lbl["my_gen"]}',
                "Изменение год к году", "Изменение год к году, %"] if yoy else [])

    head_o = ["Организация", "ИНН", "Ведомство", "Отрасль", "Холдинг", "ТБ",
              f'Портфель на конец {lbl["m2_gen"]}',
              f'Портфель на конец {lbl["m1_gen"]}',
              f'Портфель на конец {lbl["m0_gen"]}',
              f'Изменение за {lbl["m1_acc"]}', f'Изменение за {lbl["m0_acc"]}',
              "Изменение за два месяца", "Изменение за два месяца, %"] + ([
              f'Портфель на конец {lbl["my_gen"]}',
              "Изменение год к году", "Изменение год к году, %"] if yoy else [])

    def pct(d, b):
        return "" if not b else f"{d / b * 100:.1f}".replace(".", ",")

    def cut_rows(sub: pd.DataFrame, g: pd.DataFrame, title: str) -> list:
        rows = [[r.name, int(r.n), int(r.fl2), int(r.fl1), int(r.fl0),
                 int(r.d_prev), pct(r.d_prev, r.fl2),
                 int(r.d_last), pct(r.d_last, r.fl1),
                 int(r.d_two), pct(r.d_two, r.fl2),
                 f"{r.share * 100:.0f}" if r.share > 0 else ""]
                + ([int(r.fly), int(r.d_yoy), pct(r.d_yoy, r.fly)] if yoy else [])
                for r in g.itertuples()]
        fl2, fl1, fl0 = sub["fl2"].sum(), sub["fl1"].sum(), sub["fl0"].sum()
        fly = sub["fly"].sum()
        rows.append(["Итого РГС", int(sub["inn"].nunique()),
                     int(fl2), int(fl1), int(fl0),
                     int(fl1 - fl2), pct(fl1 - fl2, fl2),
                     int(fl0 - fl1), pct(fl0 - fl1, fl1),
                     int(fl0 - fl2), pct(fl0 - fl2, fl2), ""]
                    + ([int(fly), int(fl0 - fly), pct(fl0 - fly, fly)]
                       if yoy else []))
        return rows

    out = {}
    for tb, sub, cuts in variants:
        down = (sub[sub["d_two"] < -0.5]
                .sort_values("d_two", kind="stable").head(EXPORT_N))
        out[tb] = {
            "ag": cut_rows(sub, cuts["ag"], "Ведомство"),
            "hd": cut_rows(sub, cuts["hd"], "Холдинг"),
            "in": cut_rows(sub, cuts["in"], "Отрасль"),
            # ИНН строкой: в витрине это число, а в файле он должен остаться
            # кодом — у регионов 01–09 ведущий ноль иначе теряется
            "o": [[r.company_name, str(r.inn), r.agency, r.industry_name,
                   r.holding_name, r.tb_short,
                   int(r.fl2), int(r.fl1), int(r.fl0),
                   int(r.d_prev), int(r.d_last), int(r.d_two),
                   pct(r.d_two, r.fl2)]
                  + ([int(r.fly), int(r.d_yoy), pct(r.d_yoy, r.fly)]
                     if yoy else [])
                  for r in down.itertuples()],
        }
    data = {"head_c": cut_head("Ведомство"), "head_h": cut_head("Холдинг"),
            "head_i": cut_head("Отрасль"), "head_o": head_o, "vars": out,
            "period": f'{lbl["m1_nom"]} — {lbl["m0_nom"]}'}
    holder, expr = veil.hide(data, "rgs-data")
    return f'{holder}<script>window.__RGS={expr};</script>'


def _html(df: pd.DataFrame, lbl: dict, note_gap: str) -> str:
    """Собрать документ: панель, два слайда со всеми вариантами и выгрузка."""
    tbs = sorted({t for t in df["tb_short"] if t})
    def cuts_of(x: pd.DataFrame) -> dict:
        return {"ag": _cut(x, "agency"), "hd": _cut(x, "holding"),
                "in": _cut(x, "industry")}

    variants = [(ALL, df, cuts_of(df))]
    for t in tbs:
        sub = df[df["tb_short"] == t]
        variants.append((t, sub, cuts_of(sub)))
    has_holding = bool((df["holding_name"] != "").any())

    g_all = variants[0][2]["ag"]
    progress.done(
        f"РГС: портфель {df['fl0'].sum():.0f} на конец периода · за "
        f"{lbl['m0_acc']} {df['d_last'].sum():+.0f}, за {lbl['m1_acc']} "
        f"{df['d_prev'].sum():+.0f}, за два месяца {df['d_two'].sum():+.0f} · "
        f"ведомств со снижением {int((g_all['d_two'] < 0).sum())} из {len(g_all)}")
    progress.done(f"Фильтр по ТБ: {len(tbs)} — " + ", ".join(tbs))

    bodies1 = "".join(
        _var(_body_cut(sub, cuts[k], lbl, tb, k), tb, i == 0 and k == "ag", k)
        for i, (tb, sub, cuts) in enumerate(variants) for k, _n in CUTS)
    bodies2 = "".join(
        _var(_body_orgs(sub, lbl, tb, has_holding), tb, i == 0)
        for i, (tb, sub, _c) in enumerate(variants))

    s1 = _slide(
        "Сегмент РГС · региональный госсектор",
        "Снижение портфеля: ведомства, холдинги, отрасли",
        f'{C.esc(lbl["m1_nom"])} и {C.esc(lbl["m0_nom"])}'
        + (f' · год к году к {C.esc(lbl["my_dat"])}' if "my_nom" in lbl else "")
        + '<br><span>портфель получателей на конец месяца</span>',
        note_gap + bodies1,
        "Снижение за месяц — портфель на конец этого месяца минус портфель на "
        "конец предыдущего. Проценты считаются к началу своего периода. Доля в "
        "снижении — часть суммарного снижения снижающихся строк, она "
        "складывается в 100%. Поля «ведомство» в витринах нет: оно определяется "
        "по наименованию организации, неразобранные имена стоят отдельной "
        "строкой и ни к какому ведомству не приписываются. Холдинг берётся из "
        "справочника организаций, отрасль — из витрины атрибутов ЕПК "
        "(uzp_data_epk_consolidation); где они не заполнены, строка так и "
        "называется. Отрасль грубее ведомства — школа, больница и суд в ней "
        "неразличимы, — но это записанный признак, а не вывод по вывеске."
        + (f' Год к году — тот же месяц год назад ({lbl["m0_nom"]} к '
           f'{lbl["my_dat"]}): он отвечает не на «что было за эти два месяца», '
           f'а на «сезонная это просадка или портфель теряется весь год».'
           if "my_nom" in lbl else
           ' Сравнения год к году нет: месяца годичной давности в витрине не '
           'нашлось.'))
    s2 = _slide(
        "Сегмент РГС · раскрытие до организации",
        "Где именно снизился портфель",
        f'{C.esc(lbl["m1_nom"])} и {C.esc(lbl["m0_nom"])}<br>'
        '<span>порядок — от наибольшего снижения</span>',
        bodies2,
        "Портфель организации — получатели заработной платы на конец месяца. "
        "Строка считается на грейне «банк × организация»: организация, "
        "обслуживаемая в двух банках, попадает в каждый своей частью, поэтому "
        "сумма банков равна итогу по сети. Кнопка «Выгрузить в Excel» отдаёт "
        "книгу из трёх листов — ведомства, холдинги и организации — по "
        f"выбранному банку; список организаций в ней полный до {EXPORT_N} "
        "строк, а не обрезанный по размеру листа.")

    foot = (
        'Источник: витрина uzp_dwh_company_holding_metric (портфель получателей '
        f'на конец месяца, уровень {Q.LEVEL}/{Q.ORG_TYPE}) и справочник '
        f'организаций uzp_dim_company. Отобран сегмент '
        f'«{C.esc(", ".join(RGS_BIG))}» — '
        f'{C.fmt_num(df["inn"].nunique())} '
        + C.plural(int(df["inn"].nunique()), "организация", "организации",
                   "организаций")
        + f' в {C.fmt_num(len(tbs))} '
        + C.plural(len(tbs), "банке", "банках", "банках")
        + f'. Собрано {datetime.now():%d.%m.%Y, %H:%M}.')

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>РГС · снижение портфеля за {C.esc(lbl["m1_nom"])} и {C.esc(lbl["m0_nom"])}</title>
<style>{_CSS}</style>
</head>
<body>
{veil.DECODER}
<div class="deck">
{_toolbar(tbs)}
{s1}
{s2}
</div>
<div class="foot">{foot}</div>
{_export(variants, lbl)}
<script>
{_asset("xlsx.js.txt")}
</script>
{_JS}
</body>
</html>"""


def _labels(m0: str, m1: str, m2: str, my: str = "") -> dict:
    def lab(dt, case="nom"):
        ts = pd.Timestamp(dt)
        return C.month_ru(f"{ts.month:02d}.{ts.year}", case)
    extra = ({"my_nom": lab(my), "my_gen": lab(my, "gen"),
              "my_dat": lab(my, "dat")} if my else {})
    return extra | {
        "m0_nom": lab(m0), "m1_nom": lab(m1), "m2_nom": lab(m2),
        "m0_gen": lab(m0, "gen"), "m1_gen": lab(m1, "gen"),
        "m2_gen": lab(m2, "gen"),
        "m1_dat": lab(m1, "dat"), "m2_dat": lab(m2, "dat"),
        # «за сентябрь» — винительный, у месяцев он совпадает с именительным
        "m0_acc": lab(m0).split()[0], "m1_acc": lab(m1).split()[0],
    }


@dashboard("rgs_slides")
def build_dash(ctx: Context) -> str:
    """Точка входа реестра: generate_dashboard("rgs_slides", params={...}).

    params: {"months": ("2026-09-30", "2026-08-31", "2026-07-31")} — если нужно
    зафиксировать месяцы; без него берутся три последних из витрины.
    """
    p = ctx.params or {}
    months = p.get("months")
    m0, m1, m2, my = _months(ctx.engine, tuple(months) if months else None,
                             month=p.get("month", ""),
                             compare=p.get("compare", ""))
    df = _load(ctx.engine, m0, m1, m2, my)
    return _html(df, _labels(m0, m1, m2, my), _gap_note(m0, m1, m2))


def build(conn: str | None = None, months: tuple | None = None,
          month: str = "", compare: str = "",
          out_dir: Path | None = None, verbose: bool = True) -> str:
    """Собрать слайды без реестра — одной строкой из тетрадки или консоли.

    month   — отчётный месяц «2026-09» (пусто — последний в витрине);
    compare — месяц для сравнения год к году «2025-09» (пусто — год назад).
    """
    progress.enable(verbose=verbose)
    engine = get_engine(config.db_url(conn))
    m0, m1, m2, my = _months(engine, months, month=month, compare=compare)
    df = _load(engine, m0, m1, m2, my)
    html = _html(df, _labels(m0, m1, m2, my), _gap_note(m0, m1, m2))
    d = Path(out_dir or config.OUTPUT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rgs_slides_{datetime.now():%Y%m%d_%H%M%S}.html"
    path.write_text(html, encoding="utf-8")
    progress.done(f"Готово: {path} · {len(html.encode()) / 1e6:.1f} МБ")
    return str(path)


if __name__ == "__main__":        # python -m uzp_dash.dashboards.rgs_slides.view
    import sys
    a = sys.argv[1:]
    build(month=a[0] if a else "", compare=a[1] if len(a) > 1 else "")
