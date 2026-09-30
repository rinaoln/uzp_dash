"""Расчёты дэша «Работа с оттоком».

Отчёт отвечает на четыре вопроса и ни на один больше:

  1. сколько людей мы потеряли и сколько из них вернули;
  2. становится хуже или лучше — к прошлому периоду и по месяцам года;
  3. где именно теряем — какой ТБ, ГОСБ, сегмент, какая причина;
  4. чем закончилась работа по потерям — вернули, обещали и не вернули,
     работали без результата.

ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ tb_health. Там вердикт — выполнение плана, и цвет считается
от него. Плана по оттоку в витрине метрик нет вовсе (метрики только две:
портфель получателей и ФОТ), поэтому выдуманного «плана по оттоку» здесь нет:
статус уровня считается по ДИНАМИКЕ — стало хуже или лучше, чем в предыдущем
таком же периоде. Это факт из тех же данных, а не норматив, которого никто не
утверждал.

Окно отчёта — три закрытых месяца (то же, что в tb_health), сравнение — с
предыдущими тремя. История для графиков — 12 закрытых месяцев.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pandas as pd

from ... import progress
from ...render import components as C
from ..tb_health import analyze as th
from ..tb_health import forecast, segments

HIST_MONTHS = 12          # месяцев в графике динамики
CARD_TOP_N = 8            # сколько организаций называем в карточке единицы


@dataclass
class Level:
    """Один уровень отчёта: банк целиком или один ТБ."""
    key: str                       # 'sb' или tb_id строкой
    tb_id: int | None
    short: str
    full: str
    unit_label: str                # «ТБ» на уровне банка, «ГОСБ» на уровне ТБ
    totals: dict = field(default_factory=dict)      # окно и сравнение
    months: list = field(default_factory=list)      # помесячная динамика
    units: list = field(default_factory=list)       # карточки единиц
    matrix: pd.DataFrame = field(default_factory=pd.DataFrame)
    segs: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)     # свод причин ухода
    outflow: dict = field(default_factory=dict)     # списки работы (из tb_health)
    orgs: list = field(default_factory=list)        # крупнейшие потери, поимённо
    reco: list = field(default_factory=list)        # что делать — общий блок уровня
    reco_sec: dict = field(default_factory=dict)    # то же по каждому разделу
    cells: dict = field(default_factory=dict)       # клиенты внутри ячеек матрицы
    periods: list = field(default_factory=list)     # разрезы: окно и каждый месяц


def prepare(b, hist: pd.DataFrame, clients: pd.DataFrame, tb_id: int | None,
            short: str, full: str) -> Level:
    """Собрать уровень: банк (tb_id=None) или один ТБ."""
    unit_label = "ТБ" if tb_id is None else "ГОСБ"
    lvl = Level(key="sb" if tb_id is None else str(tb_id), tb_id=tb_id,
                short=short, full=full, unit_label=unit_label)
    d = b.dates or {}
    h = hist if tb_id is None else hist[hist["tb_id"] == tb_id]
    if h.empty:
        return lvl

    window = list(d.get("out_months") or [])
    lvl.totals = _totals(h, window)
    lvl.months = _months(h)
    # РАЗРЕЗЫ. Отчёт показывает окно и каждый его месяц отдельно: три месяца
    # накопительно сглаживают разовый уход, а месяц отвечает на вопрос «что
    # происходит сейчас». Считаются они из одних и тех же строк, поэтому сумма
    # трёх месяцев равна накопительному итогу, и это проверяется глазами.
    lvl.periods = _periods(h, clients, b, window, tb_id)
    base = lvl.periods[0] if lvl.periods else {}
    lvl.units = base.get("units") or []
    lvl.matrix = base.get("matrix") if base.get("matrix") is not None else pd.DataFrame()
    lvl.segs = base.get("segs") or []
    # ячейки всех разрезов в одном словаре: ключ начинается с кода разреза
    lvl.cells = {k: v for p in lvl.periods for k, v in (p.get("cells") or {}).items()}
    lvl.outflow = _work_lists(b, tb_id)
    lvl.reasons = (lvl.outflow or {}).get("reasons_block") or {}
    lvl.orgs = _orgs(lvl.outflow)
    lvl.reco_sec = _reco_sections(lvl)
    lvl.reco = _reco(lvl)
    return lvl


# --------------------------------------------------------------------------- #
# КРАСНЫЕ ЗОНЫ И КАК ИХ УБРАТЬ.
#
# «Красная зона» в этом отчёте — не оценка на глаз, а вычислимый признак:
#   * в матрице   — пары (подразделение, сегмент) с самой тёмной заливкой, то
#                   есть те, что дают заметную долю потерь уровня;
#   * в карточках — подразделения, где потери ВЫРОСЛИ к предыдущему периоду;
#   * в динамике  — месяцы, где потери заметно выше обычного уровня года.
#
# К каждой зоне отчёт обязан назвать ЦЕЛЬ — сколько человек нужно вернуть или не
# потерять, чтобы зона перестала быть красной. Цель считается из самих данных:
# разница с ближайшим «нормальным» значением. Это не план (плана по оттоку в
# витрине нет) и не норматив, а ровно то расстояние, которое отделяет зону от
# остальных.
RED_CELL_SHARE = 0.08      # доля потерь уровня, с которой ячейка красная
RED_MONTH_RATIO = 1.5      # во сколько раз месяц выше медианы года


def _reco_sections(lvl: Level) -> dict:
    """Рекомендации внутри каждого раздела: свои зоны и свои действия."""
    out = {}
    if lvl.months:
        out["trend"] = _reco_trend(lvl)
    if not lvl.matrix.empty:
        out["matrix"] = _reco_matrix(lvl)
    if lvl.units:
        out["units"] = _reco_units(lvl)
    o = lvl.outflow or {}
    if o.get("reasons"):
        out["reasons"] = _reco_reasons(lvl)
    if o.get("top_promised") or o.get("top_worked"):
        out["work"] = _reco_work(lvl)
    if lvl.orgs:
        out["orgs"] = _reco_orgs(lvl)
    return {k: v for k, v in out.items() if v}


def _reco_trend(lvl: Level) -> list:
    """Месяцы-всплески: насколько они выше обычного уровня и что это было."""
    kept = sorted(x["kept"] for x in lvl.months)
    med = kept[len(kept) // 2] if kept else 0
    red = [m for m in lvl.months if med and m["kept"] >= med * RED_MONTH_RATIO]
    out = []
    if red:
        worst = max(red, key=lambda x: x["kept"])
        over = worst["kept"] - med
        out.append({
            "do": f'Разобрать всплеск в {worst["label"]}',
            "why": (f'{_n(worst["kept"])} чел против обычных {_n(med)} за месяц — '
                    f'сверх обычного уровня {_n(over)} чел. Красных месяцев в '
                    f'периоде: {len(red)}'),
            "kind": "bad"})
        out.append({
            "do": "Понять, один это крупный клиент или общая тенденция",
            "why": ('если всплеск дали несколько клиентов — работа точечная, '
                    'в разделе «Крупнейшие потери» они названы поимённо'),
            "kind": "warn"})
    last = lvl.months[-1]
    if med and last["kept"] > med:
        out.append({
            "do": "Удержать последний закрытый месяц на обычном уровне",
            "why": (f'{last["label"]}: {_n(last["kept"])} чел при обычных '
                    f'{_n(med)} — чтобы выйти из красной зоны, нужно не потерять '
                    f'{_n(last["kept"] - med)} чел'),
            "kind": "warn"})
    out += _reco_month_cmp(lvl)
    return out


def _reco_month_cmp(lvl: Level) -> list:
    """Что делать с ростом, который видно в сравнениях месяца.

    Рост сразу в обоих сравнениях — это уже не сезонность и не разовый уход:
    так отчёт отличает тенденцию от всплеска, и формулировка действия у них
    разная. Цель в людях берётся из сравнения с годом назад: это последний
    уровень, на котором подразделение уже работало.
    """
    t = lvl.totals or {}
    mom, yoy = t.get("mom"), t.get("yoy")
    grow = [c for c in (mom, yoy) if c and c["delta"] > 0]
    if len(grow) == 2:
        return [{
            "do": "Остановить рост потерь: он идёт и к прошлому месяцу, и к прошлому году",
            "why": (f'{mom["cur_label"]}: {_n(mom["kept"])} чел — на '
                    f'{_n(mom["delta"])} больше, чем в {mom["base_label"]}, и на '
                    f'{_n(yoy["delta"])} больше, чем в {yoy["base_label"]}. Чтобы '
                    f'вернуться к уровню года назад, нужно удержать '
                    f'{_n(yoy["delta"])} чел'),
            "kind": "bad"}]
    if grow:
        c = grow[0]
        what = ("к предыдущему месяцу" if c is mom
                else "к тому же месяцу год назад")
        return [{
            "do": f'Разобрать рост потерь {what}',
            "why": (f'{c["cur_label"]}: {_n(c["kept"])} чел против '
                    f'{_n(c["base_kept"])} в {c["base_label"]} — больше на '
                    f'{_n(c["delta"])} чел; в другом сравнении роста нет, так что '
                    f'проверить нужно именно этот период'),
            "kind": "warn"}]
    return []


def _reco_matrix(lvl: Level) -> list:
    """Самые тёмные ячейки и цель по каждой — сколько вернуть, чтобы погасить."""
    m = lvl.matrix
    kept = float((lvl.totals or {}).get("kept") or 0)
    cells = (m.groupby(["unit_name", "seg_name"])["kept"].sum()
              .sort_values(ascending=False))
    if cells.empty or not kept:
        return []
    red = [(u, s, float(v)) for (u, s), v in cells.items()
           if v / kept >= RED_CELL_SHARE]
    if not red:
        return []
    out = []
    top_u, top_s, top_v = red[0]
    # цель: опуститься до следующей по весу пары — тогда зона перестаёт быть худшей
    nxt = float(cells.iloc[1]) if len(cells) > 1 else 0.0
    out.append({
        "do": f'Взяться за пару {top_u} · {top_s}',
        "why": (f'{_n(top_v)} чел — {top_v / kept * 100:.0f}% потерь уровня, '
                f'самая тёмная ячейка матрицы. Чтобы она перестала быть худшей, '
                f'нужно вернуть или не потерять {_n(max(top_v - nxt, 0))} чел'),
        "kind": "bad"})
    if len(red) > 1:
        names = ", ".join(f"{u} · {s}" for u, s, _ in red[1:4])
        fl = sum(v for _, _, v in red[1:])
        n_rest = len(red) - 1
        out.append({
            "do": "Не упускать остальные красные ячейки",
            "why": (f'ещё {n_rest} {C.plural(n_rest, "пара", "пары", "пар")} '
                    f'{C.plural(n_rest, "даёт", "дают", "дают")} {_n(fl)} чел '
                    f'({fl / kept * 100:.0f}% потерь уровня): {names}'),
            "kind": "warn"})
    out.append({
        "do": "Свести красные ячейки к среднему по матрице",
        "why": (f'если каждая из {len(red)} красных пар опустится до среднего по '
                f'матрице ({_n(float(cells.mean()))} чел), потери уровня '
                f'сократятся на {_n(sum(v for _, _, v in red) - len(red) * float(cells.mean()))} чел'),
        "kind": "warn"})
    return out


def _reco_units(lvl: Level) -> list:
    """Подразделения, где потери выросли: на сколько и до какого уровня возвращать."""
    grown = [u for u in lvl.units if (u.get("delta") or 0) > 0.5]
    if not grown:
        return [{"do": "Удержать то, что есть",
                 "why": "ни одно подразделение не увеличило потери к предыдущим "
                        "трём месяцам", "kind": "good"}]
    grown.sort(key=lambda x: -x["delta"])
    total = sum(u["delta"] for u in grown)
    top = grown[0]
    names = ", ".join(u["name"] for u in grown[:3])
    return [
        {"do": f'Начать с {lvl.unit_label} {top["name"]}',
         "why": (f'потери выросли на {_n(top["delta"])} чел — с '
                 f'{_n(top["was_kept"])} до {_n(top["kept"])}; чтобы вернуться к '
                 f'прежнему уровню, нужно удержать {_n(top["delta"])} чел'),
         "kind": "bad"},
        {"do": (f'Собрать план по {len(grown)} '
                f'{C.plural(len(grown), "подразделению", "подразделениям", "подразделениям")} '
                f'с ростом потерь'),
         "why": (f'суммарный рост {_n(total)} чел; в красной зоне: {names}'
                 + (f' и ещё {len(grown) - 3}' if len(grown) > 3 else "")),
         "kind": "warn"},
    ]


def _reco_reasons(lvl: Level) -> list:
    o = lvl.outflow or {}
    reasons = o.get("reasons") or []
    known = float(o.get("reason_known_kept") or 0)
    if not reasons or not known:
        return []
    top = reasons[0]
    out = [{"do": f'Отработать причину «{top.get("reason", "")}»',
            "why": (f'{_n(top.get("kept", 0))} чел — '
                    f'{float(top.get("kept") or 0) / known * 100:.0f}% потерь там, '
                    f'где причина зафиксирована. Это самая массовая причина ухода'),
            "kind": "bad"}]
    if len(reasons) > 1:
        second = reasons[1]
        out.append({"do": f'Затем — «{second.get("reason", "")}»',
                    "why": f'{_n(second.get("kept", 0))} чел, вторая по весу',
                    "kind": "warn"})
    cov = o.get("reason_known_n")
    n_all = o.get("n_all")
    if cov and n_all:
        out.append({"do": "Требовать, чтобы причину ухода записывали в задачу",
                    "why": (f'сейчас причина известна у {cov} организаций из '
                            f'{n_all}: по остальным разбирать нечего'),
                    "kind": "warn"})
    return out


def _reco_work(lvl: Level) -> list:
    o = lvl.outflow or {}
    promised = o.get("top_promised") or []
    worked = o.get("top_worked") or []
    out = []
    if promised:
        fl = sum(float(r.get("kept") or 0) for r in promised)
        top = max(promised, key=lambda r: float(r.get("kept") or 0))
        out.append({"do": "Вернуться к тем, кто обещал вернуть людей",
                    "why": (f'{len(promised)} '
                            f'{C.plural(len(promised), "клиент", "клиента", "клиентов")}, '
                            f'{_n(fl)} чел. Крупнейший — {top.get("name", "")}: '
                            f'{_n(top.get("kept", 0))} чел'),
                    "kind": "bad"})
    if worked:
        fl = sum(float(r.get("kept") or 0) for r in worked)
        out.append({"do": "Понять, почему работа не дала возврата",
                    "why": (f'{len(worked)} '
                            f'{C.plural(len(worked), "клиент", "клиента", "клиентов")}, '
                            f'{_n(fl)} чел: работа велась в месяцы ухода, но '
                            f'не вернулся никто'),
                    "kind": "warn"})
    return out


def _reco_orgs(lvl: Level) -> list:
    rows = lvl.orgs[:5]
    if not rows:
        return []
    fl = sum(float(r.get("kept") or 0) for r in rows)
    kept = float((lvl.totals or {}).get("kept") or 0)
    share = f' — {fl / kept * 100:.0f}% потерь уровня' if kept else ""
    return [{"do": (f'Взять в работу {len(rows)} '
                    f'{C.plural(len(rows), "крупнейшего клиента", "крупнейших клиента", "крупнейших клиентов")}'),
             "why": f'на них приходится {_n(fl)} чел{share}',
             "kind": "bad"},
            {"do": "Проверить, за каждым ли клиентом закреплён сотрудник",
             "why": ("в списке видно закрепление: где его нет, задачу некому "
                     "поставить"), "kind": "warn"}]


def _reco(lvl: Level) -> list:
    """Рекомендации уровня — из фактов этого же уровня, без вымысла.

    Каждый пункт обязан отвечать на три вопроса: что сделать, по каким числам
    это видно и почему именно сейчас. Поэтому пункт собирается ТОЛЬКО когда для
    него есть посчитанное основание: нет невыполненных обещаний — нет и пункта
    про них. Пороги здесь не про «сколько терять можно» (такого норматива нет),
    а про заметность: ниже них пункт был бы советом ни о чём.

    Порядок — по убыванию веса: сначала то, что объясняет большую часть потерь.
    """
    t = lvl.totals or {}
    if not t or not t.get("kept"):
        return []
    kept = float(t["kept"])
    out = []

    # 0. Красные зоны уровня одной строкой: сколько их и сколько в них людей
    red = _red_summary(lvl)
    if red:
        out.append(red)

    # 1. Концентрация потерь: одно подразделение объясняет заметную часть
    if lvl.units:
        top = lvl.units[0]
        share = top["kept"] / kept if kept else 0
        if share >= 0.2:
            out.append({
                "do": f'Начать с {lvl.unit_label} {top["name"]}',
                "why": (f'{_n(top["kept"])} чел безвозвратных потерь — '
                        f'{share * 100:.0f}% всех потерь уровня'),
                "kind": "bad"})

    # 2. Сегмент, в котором теряем больше всего
    if not lvl.matrix.empty:
        by_seg = lvl.matrix.groupby("seg_name")["kept"].sum().sort_values(ascending=False)
        if len(by_seg):
            seg, val = by_seg.index[0], float(by_seg.iloc[0])
            share = val / kept if kept else 0
            if share >= 0.25:
                out.append({
                    "do": f'Разобрать отток в сегменте {seg}',
                    "why": (f'{_n(val)} чел — {share * 100:.0f}% потерь уровня; '
                            f'это больше, чем во всех остальных сегментах вместе'
                            if share > 0.5 else
                            f'{_n(val)} чел — {share * 100:.0f}% потерь уровня'),
                    "kind": "bad"})

    # 3. Невыполненные обещания: есть с кого спросить и к кому вернуться
    o = lvl.outflow or {}
    promised = o.get("top_promised") or []
    if promised:
        fl = sum(float(r.get("kept") or 0) for r in promised)
        out.append({
            "do": "Вернуться к клиентам, которые обещали вернуть получателей",
            "why": (f'{len(promised)} клиентов обещали вернуть получателей и не '
                    f'вернули — {_n(fl)} чел'),
            "kind": "warn"})

    # 4. Отработка без результата — вопрос к подходу, а не к исполнителю
    worked = o.get("top_worked") or []
    if worked:
        fl = sum(float(r.get("kept") or 0) for r in worked)
        out.append({
            "do": "Разобраться, почему работа не вернула никого",
            "why": (f'{len(worked)} клиентов отработаны в месяцы ухода без '
                    f'возврата — {_n(fl)} чел'),
            "kind": "warn"})

    # 5. Главная причина ухода — если она вообще зафиксирована у заметной части
    reasons = o.get("reasons") or []
    known = float(o.get("reason_known_kept") or 0)
    if reasons and known:
        top = reasons[0]
        share = float(top.get("kept") or 0) / known if known else 0
        if share >= 0.25:
            out.append({
                "do": f'Отработать причину «{top.get("reason", "")}»',
                "why": (f'{_n(top.get("kept", 0))} чел — {share * 100:.0f}% потерь '
                        f'там, где причина ухода зафиксирована в задаче'),
                "kind": "warn"})

    # 6. Доля возврата: сравниваем только с собой же в прошлом периоде
    prev = t.get("prev") or {}
    now_share, was_share = t.get("ret_share"), prev.get("ret_share")
    if now_share is not None and was_share is not None and was_share - now_share >= 0.03:
        out.append({
            "do": "Понять, почему возвращать стало труднее",
            "why": (f'доля возврата снизилась с {was_share * 100:.0f}% до '
                    f'{now_share * 100:.0f}% к предыдущим трём месяцам'),
            "kind": "bad"})
    return out


def _red_summary(lvl: Level) -> dict | None:
    """Сводка по красным зонам уровня: где они и сколько человек в них стоит.

    Считается по тем же правилам, что и зоны внутри разделов (см. RED_*), чтобы
    число в общем блоке совпадало с тем, что читатель увидит, открыв раздел.
    """
    kept = float((lvl.totals or {}).get("kept") or 0)
    if not kept:
        return None
    cells = []
    if not lvl.matrix.empty:
        g = lvl.matrix.groupby(["unit_name", "seg_name"])["kept"].sum()
        cells = [float(v) for v in g if v / kept >= RED_CELL_SHARE]
    grown = [u for u in lvl.units if (u.get("delta") or 0) > 0.5]
    if not cells and not grown:
        return None
    parts = []
    if cells:
        parts.append(f'{len(cells)} пар «подразделение × сегмент» с '
                     f'{_n(sum(cells))} чел потерь '
                     f'({sum(cells) / kept * 100:.0f}% уровня)')
    if grown:
        parts.append(f'{len(grown)} подразделений, где потери выросли — '
                     f'суммарно на {_n(sum(u["delta"] for u in grown))} чел')
    return {"do": "Убрать красные зоны",
            "why": ("в красной зоне " + "; ".join(parts) +
                    ". Что именно делать по каждой — в рекомендациях разделов "
                    "«Матрица потерь» и «Детализация»"),
            "kind": "bad"}


def _n(v) -> str:
    return f"{float(v or 0):,.0f}".replace(",", " ")


# --------------------------------------------------------------------------- #
def _totals(h: pd.DataFrame, window: list) -> dict:
    """Итоги окна, сравнение с предыдущим окном и два сравнения по месяцам.

    Вердикт уровня считается по ОКНУ: предыдущее окно — три месяца, стоящие в
    истории прямо перед отчётным. Три месяца сглаживают уход одного крупного
    клиента, из-за которого отдельный месяц ничего не доказывает.

    Сверх этого считаются два сравнения последнего закрытого месяца: к
    предыдущему месяцу — что изменилось только что, и к тому же месяцу год
    назад — сезонность, где август сравнивается с августом, а не с июлем.
    Месяц-основание ищется ПО КАЛЕНДАРЮ, а не по позиции в истории: если его в
    витрине нет, сравнение не показывается вовсе. Соседний месяц под чужой
    подписью хуже, чем отсутствие сравнения.
    """
    months = sorted(h["report_dt"].unique())
    cur = [m for m in months if m in set(window)] or months[-3:]
    prev = [m for m in months if m < min(cur)][-len(cur):]
    now, was = _sum(h, cur), _sum(h, prev)
    out = {"months": cur, "prev_months": prev, **now,
           "prev": was,
           "delta_kept": now["kept"] - was["kept"] if was else None,
           "delta_ret_share": (now["ret_share"] - was["ret_share"]
                               if was and was.get("ret_share") is not None
                               and now.get("ret_share") is not None else None)}
    out["mom"] = _cmp_month(h, months, 1)      # месяц к месяцу
    out["yoy"] = _cmp_month(h, months, 12)     # год к году, тот же месяц
    return out


def _cmp_month(h: pd.DataFrame, months: list, back: int) -> dict | None:
    """Последний закрытый месяц против месяца на `back` месяцев раньше."""
    return _cmp_at(h, months, months[-1], back) if months else None


def _cmp_at(h: pd.DataFrame, months: list, cur, back: int) -> dict | None:
    """Месяц `cur` против месяца на `back` месяцев раньше."""
    if not months or cur is None:
        return None
    by_period = {pd.Timestamp(m).to_period("M"): m for m in months}
    base = by_period.get(pd.Timestamp(cur).to_period("M") - back)
    if base is None:
        return None
    now, was = _sum(h, [cur]), _sum(h, [base])
    if not now or not was:
        return None
    delta = now["kept"] - was["kept"]
    return {"cur": cur, "base": base, "back": back,
            "cur_label": _lbl(cur), "base_label": _lbl(base),
            "kept": now["kept"], "base_kept": was["kept"],
            "gone": now["gone"], "base_gone": was["gone"],
            "ret": now["ret"], "base_ret": was["ret"],
            "ret_share": now["ret_share"], "base_ret_share": was["ret_share"],
            "delta": delta,
            "pct": (delta / was["kept"] * 100) if was["kept"] else None}


def _shift_months(months: list, avail: list, back: int) -> list:
    """Те же месяцы на `back` месяцев раньше — только те, что есть в истории."""
    by = {pd.Timestamp(m).to_period("M"): m for m in avail}
    got = [by.get(pd.Timestamp(m).to_period("M") - back) for m in months]
    return [m for m in got if m is not None]


def _lbl(dt) -> str:
    ts = pd.Timestamp(dt)
    return f"{ts.month:02d}.{ts.year}"


def _sum(h: pd.DataFrame, months: list) -> dict:
    if not months:
        return {}
    sub = h[h["report_dt"].isin(months)]
    gone = float(sub["out_qty"].sum())
    ret = float(sub["ret_qty"].sum())
    kept = float(sub["out_kept"].sum())
    return {"gone": gone, "ret": ret, "kept": kept,
            "force": float(sub["force_qty"].sum()),
            "orgs": int(sub["n_orgs"].sum()),
            "ret_share": (ret / gone) if gone else None}


def _totals_month(h: pd.DataFrame, m) -> dict:
    """Итоги одного месяца и его сравнения: к предыдущему месяцу и к году назад.

    Форма словаря та же, что у окна: представление не должно знать, какой разрез
    оно рисует, иначе каждая карточка обросла бы условиями.
    """
    months = sorted(h["report_dt"].unique())
    by_period = {pd.Timestamp(x).to_period("M"): x for x in months}
    prev = by_period.get(pd.Timestamp(m).to_period("M") - 1)
    now = _sum(h, [m])
    was = _sum(h, [prev]) if prev is not None else {}
    return {"months": [m], "prev_months": [prev] if prev is not None else [],
            **now, "prev": was,
            "delta_kept": now["kept"] - was["kept"] if was else None,
            "delta_ret_share": (now["ret_share"] - was["ret_share"]
                                if was and was.get("ret_share") is not None
                                and now.get("ret_share") is not None else None),
            "mom": _cmp_at(h, months, m, 1),
            "yoy": _cmp_at(h, months, m, 12)}


def _periods(h: pd.DataFrame, clients: pd.DataFrame, b, window: list,
             tb_id: int | None) -> list:
    """Разрезы отчёта: окно накопительно и каждый его месяц отдельно.

    Первым идёт накопительный: он и есть вердикт уровня, на нём считаются
    рекомендации и он же открыт по умолчанию. Месяцы идут от свежего к старому —
    читателя интересует прежде всего последний закрытый.
    """
    if not window:
        return []
    ms = sorted(window)
    units, matrix, segs = _units(h, window, tb_id,
                                 n_orgs=_n_orgs(clients, tb_id, window))
    out = [{"key": "win", "short": "Три месяца",
            "label": f"Три месяца накопительно · {_lbl(ms[0])} — {_lbl(ms[-1])}",
            "months": list(window), "is_month": False,
            "totals": _totals(h, window), "units": units, "matrix": matrix,
            "segs": segs, "cells": _cells(clients, b, tb_id, window, "win")}]
    for m in sorted(window, reverse=True):
        u, mx, sg = _units(h, [m], tb_id, with_yoy=True,
                           n_orgs=_n_orgs(clients, tb_id, [m]))
        out.append({"key": _lbl(m), "short": _lbl(m),
                    "label": C.month_ru(_lbl(m)).capitalize(),
                    "months": [m], "is_month": True,
                    "totals": _totals_month(h, m), "units": u, "matrix": mx,
                    "segs": sg, "cells": _cells(clients, b, tb_id, [m], _lbl(m))})
    for p in out:
        p["unit_cards"] = _unit_payload(p, tb_id)
    return out


def _n_orgs(clients: pd.DataFrame, tb_id: int | None, months: list) -> dict:
    """Сколько РАЗНЫХ клиентов потеряла каждая единица за период."""
    if clients is None or clients.empty:
        return {}
    c = clients if tb_id is None else clients[clients["tb_id"] == tb_id]
    if months and "report_dt" in c:
        c = c[c["report_dt"].isin(months)]
    if c.empty:
        return {}
    key = "tb_id" if tb_id is None else "unit_id"
    return c.groupby(key)["inn"].nunique().to_dict()


def _unit_payload(period: dict, tb_id: int | None) -> dict:
    """Что показать в раскрытой карточке подразделения.

    Клиентов сюда не кладём: они уже лежат в перечнях ячеек матрицы, и скрипт
    собирает список единицы из них. Дублировать тот же список второй раз —
    лишний мегабайт в файле ради тех же имён.
    """
    m = period.get("matrix")
    by_unit: dict = {}
    if m is not None and not m.empty:
        for r in m.itertuples():
            by_unit.setdefault(int(r.unit_id), []).append(
                {"seg": str(r.seg_name), "kept": float(r.kept)})
    out = {}
    for u in period.get("units") or []:
        segs = sorted(by_unit.get(u["id"], []), key=lambda x: -x["kept"])
        out[f'{period["key"]}|{u["id"]}'] = {
            "name": u["name"], "gone": u["gone"], "ret": u["ret"],
            "kept": u["kept"], "orgs": u["orgs"],
            "ret_share": u.get("ret_share"),
            "delta": u.get("delta"), "was": u.get("was_kept"),
            # подпись базы сравнения готовим здесь: «к 01.2026, 02.2026, 03.2026»
            # в раскрытой карточке читается как случайный набор дат
            "prev": ("прошлым трём месяцам" if not period.get("is_month")
                     else C.month_ru(u.get("prev_label", ""), "dat")),
            "yoy": u.get("yoy_delta"), "yoy_was": u.get("yoy_kept"),
            "yoy_label": C.month_ru(u.get("yoy_label", ""), "dat"),
            "period": period["label"], "segs": segs}
    return out


def _months(h: pd.DataFrame) -> list:
    """Помесячная динамика за последние HIST_MONTHS месяцев."""
    g = (h.groupby("report_dt")[["out_qty", "ret_qty", "out_kept"]].sum()
          .sort_index().tail(HIST_MONTHS))
    out = []
    for dt, r in g.iterrows():
        ts = pd.Timestamp(dt)
        out.append({"dt": dt,
                    "label": f"{ts.month:02d}.{ts.year}",
                    "short": f"{ts.month:02d}.{ts.year % 100:02d}",
                    "gone": float(r["out_qty"]), "ret": float(r["ret_qty"]),
                    "kept": float(r["out_kept"])})
    return out


def _units(h: pd.DataFrame, window: list, tb_id: int | None,
           with_yoy: bool = False, n_orgs: dict | None = None):
    """Карточки единиц и матрица «единица × сегмент» по безвозвратным потерям.

    Период задаётся списком месяцев: окно целиком или один месяц — расчёт один и
    тот же. Предыдущий период берётся СТРОГО ДО начала текущего: для месяца это
    предыдущий месяц, а не «все прочие месяцы истории», иначе апрелю в пару встал
    бы июнь и стрелка динамики показывала бы в обратную сторону.
    """
    cur = h[h["report_dt"].isin(window)] if window else h
    if cur.empty:
        return [], pd.DataFrame(), []
    key = "tb_id" if tb_id is None else "unit_id"
    name_col = "tb_name" if tb_id is None else "unit_name"

    g = cur.groupby([key, name_col], dropna=False).agg(
        gone=("out_qty", "sum"), ret=("ret_qty", "sum"),
        kept=("out_kept", "sum"), orgs=("n_orgs", "sum")).reset_index()
    avail = sorted(set(h["report_dt"]))
    prev_months = ([m for m in avail if m < min(window)][-len(window):]
                   if window else [])
    prev = (h[h["report_dt"].isin(prev_months)]
            .groupby(key)["out_kept"].sum().to_dict() if prev_months else {})
    # год к году считаем только для месячного разреза: у окна из трёх месяцев
    # такое сравнение уже есть в разделе «Динамика», а в карточке оно заняло бы
    # третью строку ради того же факта
    yoy_months = _shift_months(window, avail, 12) if with_yoy and window else []
    yoy = (h[h["report_dt"].isin(yoy_months)]
           .groupby(key)["out_kept"].sum().to_dict() if yoy_months else {})

    units = []
    for r in g.itertuples():
        uid = int(getattr(r, key))
        was = float(prev.get(uid, 0.0))
        # клиентов считаем по ИНН из клиентского среза: в истории n_orgs лежит
        # помесячно, и сумма за три месяца считала бы одного клиента трижды
        orgs = int((n_orgs or {}).get(uid, r.orgs))
        u = {"id": uid, "name": str(getattr(r, name_col) or uid),
             "gone": float(r.gone), "ret": float(r.ret),
             "kept": float(r.kept), "orgs": orgs,
             "ret_share": (float(r.ret) / float(r.gone)) if r.gone else None,
             "was_kept": was, "delta": float(r.kept) - was if prev else None,
             "prev_label": ", ".join(_lbl(m) for m in prev_months)}
        if yoy_months:
            ago = float(yoy.get(uid, 0.0))
            u["yoy_kept"] = ago
            u["yoy_delta"] = float(r.kept) - ago
            u["yoy_label"] = ", ".join(_lbl(m) for m in yoy_months)
        units.append(u)
    units.sort(key=lambda x: -x["kept"])

    segs = [s for s in segments.ORDER if s in set(cur["seg_name"].dropna())]
    matrix = (cur.groupby([key, name_col, "seg_name"], dropna=False)["out_kept"]
                 .sum().reset_index()
                 .rename(columns={key: "unit_id", name_col: "unit_name",
                                  "out_kept": "kept"}))
    return units, matrix, segs


CELL_TOP_N = 12           # клиентов в раскрытой ячейке матрицы


def _cells(clients: pd.DataFrame, b, tb_id: int | None, months: list,
           pkey: str) -> dict:
    """Клиенты внутри каждой ячейки матрицы: {«единица|сегмент»: {...}}.

    Считается из той же витрины и тем же фильтром, что и сама матрица, только на
    грейн ниже — поэтому сумма клиентов ячейки равна её числу, и это можно
    проверить глазами прямо в отчёте. Брать клиентов из расчёта прогноза нельзя:
    он живёт на другом окне и дал бы другие числа под тем же заголовком.

    В раскрытой плашке показываются CELL_TOP_N крупнейших, остальные сворачиваются
    в строку «ещё N клиентов»: список на триста строк в управленческом отчёте не
    читают, а вес файла он утраивает.
    """
    if clients is None or clients.empty:
        return {}
    c = clients if tb_id is None else clients[clients["tb_id"] == tb_id]
    if months and "report_dt" in c:
        c = c[c["report_dt"].isin(months)]
    if c.empty:
        return {}
    key = "tb_id" if tb_id is None else "unit_id"
    # на уровне банка единица — ТБ, и организация, обслуживаемая в нескольких его
    # ГОСБ, обязана стать ОДНОЙ строкой: иначе крупнейший клиент ячейки
    # рассыпается на части и в перечень не попадает
    g = (c.groupby([key, "seg_name", "inn"], as_index=False)
          [["out_qty", "ret_qty", "out_kept"]].sum())
    names = _name_by_inn(b)
    out: dict = {}
    for (uid, seg), part in g.groupby([key, "seg_name"], dropna=False):
        part = part.sort_values("out_kept", ascending=False)
        rows = [{"name": names.get(int(r.inn)) or f"Орг. {int(r.inn)}",
                 "gone": float(r.out_qty), "ret": float(r.ret_qty),
                 "kept": float(r.out_kept)}
                for r in part.head(CELL_TOP_N).itertuples()]
        tail = part.iloc[CELL_TOP_N:]
        out[f"{pkey}|{int(uid)}|{seg}"] = {
            "seg": str(seg), "n": int(len(part)),
            "kept": float(part["out_kept"].sum()),
            "gone": float(part["out_qty"].sum()),
            "ret": float(part["ret_qty"].sum()), "rows": rows,
            "rest_n": int(len(tail)), "rest_kept": float(tail["out_kept"].sum())}
    return out


def _name_by_inn(b) -> dict:
    """ИНН → название организации.

    Имя одно на ИНН: в перечне ячейки уровня банка клиент уже сведён по всем
    своим ГОСБ, и выбирать между их написаниями нечего.
    """
    o = getattr(b, "orgs", None)
    if o is None or o.empty or "company_name" not in o:
        return {}
    out = {}
    for r in o.itertuples():
        nm = str(getattr(r, "company_name", "") or "").strip()
        if nm:
            out.setdefault(int(r.inn), nm)
    return out


def load_clients(ctx, b) -> pd.DataFrame:
    """Отток на грейне (ГОСБ, сегмент, ИНН) за отчётное окно — для матрицы."""
    from ...db import read_sql
    from . import queries as Q
    from ..tb_health import bank as th_bank

    d = b.dates or {}
    progress.step("Клиенты внутри ячеек матрицы потерь")
    c = read_sql(ctx.engine, Q.OUTFLOW_CLIENTS,
                 {"out_min": th_bank.OUT_MIN_QTY, "out_from": d.get("out_from"),
                  "ref_closed": d.get("ref_closed")})
    if c.empty:
        progress.warn("Клиентов за окно не нашлось — ячейки матрицы не раскроются")
        return c
    c["report_dt"] = pd.to_datetime(c["report_dt"]).dt.date
    c["unit_id"] = c["unit_id"].astype("int64")
    c["tb_id"] = c["tb_id"].astype("int64")
    c["inn"] = c["inn"].astype("int64")
    for col in ("out_qty", "ret_qty", "out_kept"):
        c[col] = forecast.num(c, col)
    progress.done(f"Клиенты ячеек: {len(c)} строк · "
                  f"{c['inn'].nunique()} организаций")
    return c


def _work_lists(b, tb_id: int | None) -> dict:
    """Списки работы по оттоку — расчётом дэша tb_health, без копии логики.

    Группы «обещали вернуться, но не вернулись», «отработали, но без результата»
    и свод причин ухода считаются там из задач и витрины возвратов. Повторять
    двести строк разбора текста ради второго отчёта нельзя: разойдутся не только
    формулировки, но и числа. Поэтому сюда собирается ровно тот минимум полей,
    который читает `tb_health.analyze._outflow_top`, — и вызывается он сам.
    """
    src = "new_gosb_id" if tb_id is not None else "tb_id"
    fc = b.orgs_fc
    if fc is None or fc.empty:
        return {}
    if tb_id is not None:
        fc = fc[fc["new_gosb_id"].map(b.tb_of).eq(tb_id)]
    else:
        fc = fc.assign(tb_id=fc["new_gosb_id"].map(b.tb_of))
        fc = fc.dropna(subset=["tb_id"])
        fc["tb_id"] = fc["tb_id"].astype("int64")
    if fc.empty:
        return {}

    detail = b.orgs if tb_id is None else b.orgs[b.orgs["new_gosb_id"].map(b.tb_of).eq(tb_id)]
    if tb_id is None and "tb_id" not in detail:
        detail = detail.assign(tb_id=detail["new_gosb_id"].map(b.tb_of))
    names = _unit_names(b, tb_id)
    a = SimpleNamespace(orgs_fc=fc, unit_src=src, gosb_gap=names, detail=detail,
                        insights={}, dates=b.dates)
    return th._outflow_top(a, b.fmonths, b.promises, b.tb_of)


def _unit_names(b, tb_id: int | None) -> pd.DataFrame:
    """Кадр «единица → имя» в том виде, в каком его ждёт расчёт tb_health."""
    if tb_id is None:
        rows = [{"unit_id": int(r.tb_id), "unit_name": str(r.tb_short_name)}
                for r in b.tbs.itertuples()]
    else:
        rows = [{"unit_id": int(gid), "unit_name": str(name)}
                for gid, name in (b.gosb_name or {}).items()
                if b.tb_of.get(int(gid)) == tb_id]
    return pd.DataFrame(rows or [{"unit_id": 0, "unit_name": ""}])


def _orgs(outflow: dict) -> list:
    """Единый поимённый список потерь: обе группы работы в одной таблице."""
    seen, out = set(), []
    for kind, key in (("обещали вернуться", "top_promised"),
                      ("отработали без результата", "top_worked")):
        for r in (outflow or {}).get(key) or []:
            k = (r.get("unit"), r.get("inn"))
            if k in seen:
                continue
            seen.add(k)
            out.append({**r, "group": kind})
    out.sort(key=lambda x: -float(x.get("kept") or 0))
    return out


def load_history(ctx, b) -> pd.DataFrame:
    """История оттока по (ГОСБ, месяц, сегмент) + имена единиц."""
    from ...db import read_sql
    from . import queries as Q
    from ..tb_health import bank as th_bank

    d = b.dates
    closed = pd.Timestamp(d["ref_closed"])
    hist_from = (closed.to_period("M") - (HIST_MONTHS + 2)).to_timestamp().date()
    progress.step(f"История оттока с {hist_from} по {d['ref_closed']}")
    h = read_sql(ctx.engine, Q.OUTFLOW_HISTORY,
                 {"out_min": th_bank.OUT_MIN_QTY, "hist_from": hist_from,
                  "ref_closed": d["ref_closed"]})
    if h.empty:
        progress.warn("Истории оттока за окно не нашлось — отчёт будет пустым")
        return h
    h["report_dt"] = pd.to_datetime(h["report_dt"]).dt.date
    h["unit_id"] = h["unit_id"].astype("int64")
    h["tb_id"] = h["tb_id"].astype("int64")
    h["unit_name"] = h["unit_id"].map(b.gosb_name).fillna(h["unit_id"].astype(str))
    tb_name = {int(r.tb_id): str(r.tb_short_name) for r in b.tbs.itertuples()}
    h["tb_name"] = h["tb_id"].map(tb_name)
    h = h.dropna(subset=["tb_name"])          # ЦА и чужие ГОСБ в отчёт не идут
    for c in ("out_qty", "ret_qty", "out_kept", "force_qty", "n_orgs"):
        h[c] = forecast.num(h, c)
    progress.done(f"История оттока: {len(h)} строк · "
                  f"{h['report_dt'].nunique()} месяцев · "
                  f"{h['unit_id'].nunique()} ГОСБ · "
                  f"ушло {h['out_qty'].sum():,.0f} чел".replace(",", " "))
    return h
