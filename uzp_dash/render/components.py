"""Переиспользуемые apple-design компоненты. Каждый возвращает HTML-строку.

Дэши собирают страницу из этих компонентов, не дублируя вёрстку.
"""
from __future__ import annotations

import html as _html
import math
from typing import Iterable, Sequence


def esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def fmt_num(v, unit: str = "", digits: int = 0) -> str:
    """Число с разрядами (тонкий пробел) и опциональной единицей."""
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{digits}f}".replace(",", " ")
    except (TypeError, ValueError):
        return esc(v)
    return f"{s} {esc(unit)}".strip()


def status_of(exec_pct: float | None) -> str:
    """good/warn/bad по проценту выполнения плана (доля, 1.0 = 100%)."""
    if exec_pct is None:
        return "warn"
    if exec_pct >= 1.0:
        return "good"
    if exec_pct >= 0.95:
        return "warn"
    return "bad"


def badge(text: str, kind: str = "good") -> str:
    return f'<span class="badge {esc(kind)}">{esc(text)}</span>'


def kpi(label: str, value: str, delta: str = "", delta_kind: str = "text-2") -> str:
    color = {
        "good": "var(--good)", "warn": "var(--warn)", "bad": "var(--bad)",
    }.get(delta_kind, "var(--text-2)")
    delta_html = f'<div class="delta" style="color:{color}">{esc(delta)}</div>' if delta else ""
    return (
        '<div class="card kpi">'
        f'<div class="label">{esc(label)}</div>'
        f'<div class="value">{value}</div>'
        f"{delta_html}"
        "</div>"
    )


def meter(exec_pct: float | None) -> str:
    kind = status_of(exec_pct)
    color = {"good": "var(--good)", "warn": "var(--warn)", "bad": "var(--bad)"}[kind]
    pct = 0 if exec_pct is None else max(0, min(exec_pct, 1.2)) * 100
    return f'<div class="meter"><span style="width:{pct:.1f}%;background:{color}"></span></div>'


def table(headers: Sequence[str], rows: Iterable[Sequence[str]], num_cols: Sequence[int] = ()) -> str:
    num = set(num_cols)
    head = "".join(
        f'<th class="{"num" if i in num else ""}">{esc(h)}</th>' for i, h in enumerate(headers)
    )
    body = []
    for r in rows:
        cells = "".join(
            f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r)
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def card(inner: str, cls: str = "") -> str:
    return f'<div class="card {esc(cls)}">{inner}</div>'


def section(title: str, inner: str, eyebrow: str = "", desc: str = "") -> str:
    """Раздел отчёта. `desc` — подстрочник под заголовком.

    Класс `section-desc` и место сразу после `<h2>` выбраны не случайно: скрипт
    дизайна сворачивает разделы в `<details>` и переносит в шапку именно такой
    элемент. Свои описания он знает только для исходных разделов (по заголовку),
    поэтому новым разделам подстрочник отдаём готовым — иначе пришлось бы править
    сам скрипт, который держится копией макета.
    """
    eb = f'<div class="eyebrow">{esc(eyebrow)}</div>' if eyebrow else ""
    ds = f'<p class="section-desc">{esc(desc)}</p>' if desc else ""
    return f'<section>{eb}<h2>{esc(title)}</h2>{ds}{inner}</section>'


def heat_bg(exec_pct: float | None) -> str:
    """Фон ячейки тепловой карты по выполнению плана (доля).

    Границы те же, что у `status_of`, и это обязательно: одна и та же цифра стоит
    в ячейке матрицы, на карточке единицы и в бейдже рядом с ней, и разъезжаться
    им нельзя. Раньше красным закрашивалось всё ниже 100%, поэтому 97% в матрице
    читались как провал, а тем же 97% в бейдже соответствовал жёлтый.

    Внутри красной зоны заливка остаётся градиентом: 0.75 и ниже — насыщенный
    красный, у границы 0.95 — едва заметный. Матрица для того и нужна, чтобы
    видеть глубину провала, а не только его факт.
    """
    if exec_pct is None:
        return "transparent"
    if exec_pct >= 1.0:
        return "color-mix(in srgb, var(--good) 22%, transparent)"
    if exec_pct >= 0.95:
        return "color-mix(in srgb, var(--warn) 20%, transparent)"
    x = max(0.0, min((exec_pct - 0.75) / 0.20, 1.0))
    return f"color-mix(in srgb, var(--bad) {int(40 * (1 - x)) + 14}%, transparent)"


def heat_matrix(rows_id_label: list[tuple], seg_names: list[str], cells: dict,
                unit_head: str = "ГОСБ", ranks: dict | None = None,
                cell_click: str = "") -> str:
    """Тепловая карта единица×сегмент.

    rows_id_label — [(row_key, label), ...]; cells[(row_key, seg)] = (exec, nedobor).

    `ranks` — {row_key: (место, всего)}: колонка ранга ТБ в сети. Есть только там,
    где единица строки — ТБ; у ГОСБ ранга в витрине нет, и колонка не рисуется.

    `cell_click` — имя JS-функции, открывающей разбор ячейки. Задано — ячейка с
    числом становится кнопкой и получает ключ «уровень|единица|сегмент».
    """
    rank_head = '<th class="num" title="место ТБ в сети по выполнению плана за ' \
                'закрытый месяц">ранг</th>' if ranks else ""
    head = (f'<th>{esc(unit_head)}</th>{rank_head}'
            + "".join(f'<th class="num">{esc(s)}</th>' for s in seg_names))
    rows = []
    for key, label in rows_id_label:
        tds = [f'<td>{esc(label)}</td>']
        if ranks:
            rk = ranks.get(key)
            tds.append(f'<td class="num rank">{rk[0]}<i>/{rk[1]}</i></td>' if rk
                       else '<td class="num" style="color:var(--text-2)">—</td>')
        for s in seg_names:
            cell = cells.get((key, s))
            if not cell or cell[0] is None:
                tds.append('<td class="num" style="color:var(--text-2)">—</td>')
            else:
                ex, ned = cell
                bg = heat_bg(ex)
                # У границ цвета печатаем десятую долю: округление до целого
                # перебрасывает число через границу, и ячейка «100%» выглядела бы
                # выполненной при недоборе плана, а «95%» — жёлтой при красной заливке
                pct = (f"{math.floor(ex * 1000) / 10:.1f}%"
                       if (0.995 <= ex < 1) or (0.945 <= ex < 0.95)
                       else f"{ex*100:.0f}%")
                # клик открывает состав ячейки; клавиатура работает наравне с мышью
                # подсказка при наведении: «−5» у выполненного плана читалось бы
                # как отклонение, хотя это перевыполнение
                gap = (f"отклонение от плана −{ned:.0f} чел" if ned > 0.5
                       else f"план выполняется с запасом {abs(ned):.0f} чел"
                       if ned < -0.5 else "план выполняется")
                if cell_click:
                    ck = f"{key}|{s}"
                    act = (f' role="button" tabindex="0" '
                           f'onclick="{cell_click}(this)" '
                           f'onkeydown="if(event.key===\'Enter\'||event.key===\' \')'
                           f'{{event.preventDefault();{cell_click}(this);}}" '
                           f'data-cell="{esc(ck)}"')
                    cls = "num heat hit"
                    hint = f"{gap} · клик — клиенты со снижением за год"
                else:
                    act, cls, hint = "", "num heat", gap
                tds.append(
                    f'<td class="{cls}" style="background:{bg}" '
                    f'title="{esc(hint)}"{act}>{pct}</td>'
                )
        rows.append(f'<tr>{"".join(tds)}</tr>')
    return (f'<div style="overflow-x:auto"><table class="matrix">'
            f'<thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def projection_bar(attract: float, retention: float, gap: float) -> str:
    """Как закрывается НЕДОБОР (масштаб = разрыв до плана, не весь план).
    Подписи — в легенде под полосой (сегменты бывают узкими)."""
    attract = max(0.0, attract); retention = max(0.0, retention); gap = max(gap, 1.0)
    covered = attract + retention
    scale = max(gap, covered)
    def w(x): return f"{x / scale * 100:.2f}%"

    segs = (f'<div class="proj-seg attract" style="width:{w(attract)}" title="Привлечь"></div>'
            f'<div class="proj-seg retention" style="width:{w(retention)}" title="Вернуть"></div>')
    legend = [("attract", "Привлечь", attract), ("retention", "Вернуть", retention)]
    if covered < gap:                    # разрыв закрыт не полностью
        segs += f'<div class="proj-seg rest" style="width:{w(gap-covered)}"></div>'
        legend.append(("rest", "не хватает", gap - covered))
        tail = ""
    else:                                # закрыт с запасом
        tail = f' · с запасом +{fmt_num(covered - gap)}'
    plan_pos = f"{gap / scale * 100:.2f}%"
    leg_html = "".join(
        f'<span class="lg"><i class="{cls}"></i>{esc(name)} <b>+{fmt_num(val)}</b></span>'
        if cls != "rest" else
        f'<span class="lg"><i class="{cls}"></i>{esc(name)} <b>{fmt_num(val)}</b></span>'
        for cls, name, val in legend
    )
    return (f'<div class="proj">{segs}'
            f'<div class="proj-plan" style="left:{plan_pos}"><span>план</span></div></div>'
            f'<div class="proj-legend">{leg_html}<span class="lg-note">масштаб = отклонение от плана {fmt_num(gap)} чел{tail}</span></div>')


def hbars(items: list[tuple[str, float]], unit: str = "") -> str:
    """Мини горизонтальные полоски (напр. активности по типу/статусу)."""
    if not items:
        return '<div class="sub" style="font-size:14px">нет данных</div>'
    mx = max((v for _, v in items), default=1) or 1
    rows = []
    for label, val in items:
        rows.append(
            '<div class="hbar">'
            f'<div class="hbar-l">{esc(label)}</div>'
            f'<div class="hbar-track"><span style="width:{val/mx*100:.1f}%"></span></div>'
            f'<div class="hbar-v">{fmt_num(val, unit)}</div>'
            '</div>'
        )
    return "".join(rows)


def _ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """Круглые отметки шкалы в диапазоне [lo, hi]."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    first = math.ceil(lo / step) * step
    out, v = [], first
    while v <= hi + step * 0.001:
        out.append(v)
        v += step
    return out or [lo, hi]


# Геометрия графика в координатах viewBox.
#
# Снизу оставлено место под ДВЕ строки подписей: месяц и отклонение этого месяца от
# плана. Отклонение стоит числом, а не отдельным графиком: два графика заставляли
# читателя сопоставлять их глазами по вертикали, а вопрос «выполнен план или нет»
# решается одним числом под точкой.
_CW, _CH = 760, 250          # ширина/высота полотна
_PL, _PR, _PT, _PB = 72, 58, 26, 54   # поля: слева шкала, справа подписи линий, снизу два ряда подписей


def _x_of(i: int, n: int) -> float:
    """Центр i-го месяца по горизонтали."""
    inner = _CW - _PL - _PR
    step = inner / max(n, 1)
    return _PL + step * (i + 0.5)


def _grid(ticks: list[float], y_of, fmt=lambda v: fmt_num(v)) -> str:
    """Горизонтальные линии шкалы с подписями — приглушённые, как фон."""
    out = []
    for t in ticks:
        y = y_of(t)
        out.append(
            f'<line x1="{_PL}" y1="{y:.1f}" x2="{_CW - _PR}" y2="{y:.1f}" '
            f'stroke="var(--separator)" stroke-width="1"/>'
            f'<text x="{_PL - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="var(--text-2)">{esc(fmt(t))}</text>')
    return "".join(out)


def _months_axis(rows: list[dict], row_label: str = "") -> str:
    """Два ряда подписей под графиком: месяц и его отклонение от плана.

    Отклонение подписано цветом статуса и знаком, поэтому «выполнен план в этом
    месяце или нет» читается по нижней строке, не поднимая глаз на линии.

    `row_label` — подпись нижнего ряда в левом поле: без неё числа под месяцами
    приходится расшифровывать по тексту под графиком, а единицы у них свои.
    """
    n = len(rows)
    every = 1 if n <= 12 else 2
    out = []
    if row_label:
        out.append(f'<text x="{_PL - 8}" y="{_CH - 14}" text-anchor="end" '
                   f'font-size="10" fill="var(--text-2)">{esc(row_label)}</text>')
    for i, r in enumerate(rows):
        if i % every:
            continue
        x = _x_of(i, n)
        out.append(f'<text x="{x:.1f}" y="{_CH - 32}" text-anchor="middle" '
                   f'font-size="11" fill="var(--text-2)">{esc(r["short"])}</text>')
        dv = r["fact"] - r["plan"]
        col = "var(--good)" if dv >= 0 else "var(--bad)"
        sign = "+" if dv >= 0 else "−"
        out.append(f'<text x="{x:.1f}" y="{_CH - 14}" text-anchor="middle" '
                   f'font-size="10.5" font-weight="600" fill="{col}">'
                   f'{esc(sign + fmt_num(abs(dv)))}</text>')
    return "".join(out)


def trend_plan_fact(rows: list[dict]) -> str:
    """Портфель помесячно: факт линией, план — пунктиром.

    Две линии, а не столбцы: план и факт отличаются на проценты, и столбцы от нуля
    были бы неразличимы. У линий шкала может не начинаться с нуля — тогда это
    подписывается прямо под графиком, чтобы разрыв не читался как обвал.

    Идентичность серий держится не только цветом: факт — сплошная линия с точками,
    план — пунктир, плюс легенда. При печати и у дальтоников различие сохраняется.
    """
    if not rows:
        return ""
    vals = [v for r in rows for v in (r["plan"], r["fact"]) if v]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.18 or max(hi * 0.02, 1)
    lo, hi = max(lo - pad, 0), hi + pad
    n = len(rows)

    def y_of(v):
        return _PT + (_CH - _PT - _PB) * (1 - (v - lo) / (hi - lo or 1))

    def path(key):
        return " ".join(f'{_x_of(i, n):.1f},{y_of(r[key]):.1f}'
                        for i, r in enumerate(rows))

    fact_pts = path("fact")
    plan_pts = path("plan")
    dots, vals = [], []
    for i, r in enumerate(rows):
        x, y = _x_of(i, n), y_of(r["fact"])
        ex = (r["fact"] / r["plan"] * 100) if r["plan"] else 0
        dots.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="var(--accent)" '
            f'stroke="var(--surface-solid)" stroke-width="2">'
            f'<title>{esc(r["label"])}: факт {fmt_num(r["fact"])} · план '
            f'{fmt_num(r["plan"])} · выполнение {ex:.0f}%</title></circle>')
        # значение факта над точкой: управляющему нужна величина, а не только форма
        # кривой. Ниже плана — подпись уходит вверх, выше — тоже вверх, но от точки,
        # поэтому линия плана её не перечёркивает
        vals.append(
            f'<text x="{x:.1f}" y="{y - 11:.1f}" text-anchor="middle" font-size="9.5" '
            f'font-weight="600" fill="var(--text)">{esc(fmt_num(r["fact"]))}</text>')
    # подписи прямо у концов линий: надёжнее легенды, её приходится сопоставлять
    ly_f, ly_p = y_of(rows[-1]["fact"]), y_of(rows[-1]["plan"])
    if abs(ly_f - ly_p) < 14:            # линии сошлись — разводим подписи
        ly_p = ly_f + 14
    ends = (f'<text x="{_CW - _PR + 6}" y="{ly_f + 4:.1f}" font-size="12" '
            f'font-weight="700" fill="var(--accent)">факт</text>'
            f'<text x="{_CW - _PR + 6}" y="{ly_p + 4:.1f}" font-size="12" '
            f'font-weight="600" fill="var(--text-2)">план</text>')
    cut = ("" if lo <= 0 else
           '<div class="ch-note">Шкала не начинается с нуля: масштаб подобран под '
           'размах изменений. Высота линии над осью величину портфеля не отражает. '
           'Под месяцем — отклонение факта от плана.</div>')
    # единицы у шкалы и у нижнего ряда: без подписи числа на графике читаются как
    # «какие-то значения», а получатели (чел) и отклонение (тоже чел, но со знаком)
    # стоят в разных местах и в одну единицу не сливаются
    unit = (f'<text x="{_PL - 8}" y="{_PT - 10}" text-anchor="end" font-size="10.5" '
            f'font-weight="600" fill="var(--text-2)">чел</text>')
    return (
        '<div class="chart">'
        f'<svg viewBox="0 0 {_CW} {_CH}" role="img" preserveAspectRatio="xMidYMid meet" '
        f'aria-label="Портфель получателей помесячно, человек: факт, план и '
        f'отклонение от плана">'
        + unit
        + _grid(_ticks(lo, hi), y_of)
        + f'<polyline points="{plan_pts}" fill="none" stroke="var(--text-2)" '
          f'stroke-width="2" stroke-dasharray="6 4" stroke-linejoin="round"/>'
        + f'<polyline points="{fact_pts}" fill="none" stroke="var(--accent)" '
          f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        + "".join(dots) + "".join(vals) + ends
        + _months_axis(rows, "± к плану, чел")
        + '</svg>' + cut + '</div>'
    )


def trend_delta(rows: list[dict]) -> str:
    """Выполнение плана помесячно: столбцы «факт минус план» от нуля.

    Отдельный график, а не вторая пара линий: на линиях разрыв в проценты не виден,
    а именно он и есть ответ на вопрос «сделали план или нет». Ноль — общая база,
    поэтому длина столбца честно кодирует величину отклонения.
    """
    if not rows:
        return ""
    deltas = [r["fact"] - r["plan"] for r in rows]
    mx = max((abs(x) for x in deltas), default=0) or 1
    n = len(rows)
    top, bot = _PT, _CH - _PB
    zero = (top + bot) / 2

    def y_of(v):
        return zero - (bot - top) / 2 * (v / mx)

    inner = (_CW - _PL - _PR) / max(n, 1)
    bw = min(inner * 0.55, 34)
    bars = []
    for i, (r, dv) in enumerate(zip(rows, deltas)):
        x = _x_of(i, n) - bw / 2
        y = min(y_of(dv), zero)
        h = max(abs(zero - y_of(dv)), 1.5)
        col = "var(--good)" if dv >= 0 else "var(--bad)"
        ex = (r["fact"] / r["plan"] * 100) if r["plan"] else 0
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="3" '
            f'fill="{col}"><title>{esc(r["label"])}: {"+" if dv >= 0 else "−"}'
            f'{fmt_num(abs(dv))} чел к плану · выполнение {ex:.0f}%</title></rect>')
    ticks = [t for t in _ticks(-mx, mx, 3) if abs(t) > mx * 0.02]
    return (
        '<div class="chart">'
        f'<svg viewBox="0 0 {_CW} {_CH}" role="img" preserveAspectRatio="xMidYMid meet" '
        f'aria-label="Отклонение факта от плана помесячно">'
        + _grid(ticks, y_of, fmt=lambda v: ("+" if v > 0 else "−") + fmt_num(abs(v)))
        + f'<line x1="{_PL}" y1="{zero:.1f}" x2="{_CW - _PR}" y2="{zero:.1f}" '
          f'stroke="var(--text-2)" stroke-width="1.5"/>'
        + "".join(bars) + _months_axis(rows)
        + '</svg></div>'
    )


def trend_table(rows: list[dict]) -> str:
    """Те же 12 месяцев числами — под свёрткой.

    Нужна не для красоты: график читается глазом, а числа читаются точно, и для
    печати, скринридера и спора о конкретном месяце нужна именно таблица.
    """
    if not rows:
        return ""
    body = []
    for r in rows:
        dv = r["fact"] - r["plan"]
        ex = (r["fact"] / r["plan"]) if r["plan"] else None
        col = "var(--good)" if dv >= 0 else "var(--bad)"
        body.append([esc(r["label"]), fmt_num(r["fact"]), fmt_num(r["plan"]),
                     f'<b style="color:{col}">{"+" if dv >= 0 else "−"}'
                     f'{fmt_num(abs(dv))}</b>',
                     f'{ex * 100:.0f}%' if ex is not None else "—"])
    return ('<details class="ch-tbl"><summary>Показать в табличном виде</summary>'
            + table(["месяц", "факт, чел", "план, чел", "отклонение, чел",
                     "выполнение"], body, num_cols=[1, 2, 3, 4])
            + '</details>')


def narrative_html(text: str) -> str:
    """Лёгкий markdown → HTML: **жирный**, абзацы, подзаголовки **Х.**."""
    text = esc(text)
    text = _bold(text)
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    out = []
    for p in parts:
        if p.startswith("<b>") and p.endswith("</b>"):
            out.append(f'<h3 style="margin:18px 0 8px">{p[3:-4]}</h3>')
        else:
            out.append(f'<p style="margin:0 0 10px;color:var(--text-2);font-size:16px">{p}</p>')
    return "".join(out)


def _bold(t: str) -> str:
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)


# поля, которые повторяются из строки в строку: их выносим в словари
# (первые буквы имён различны — они же ключи словаря в payload)
_PACK_DICT = ("lever", "gosb", "seg", "reason", "action", "emp")


def _pack(rows: list[dict]) -> str:
    """Строки таблицы → JS-выражение, которое разворачивается в те же объекты.

    Повторяющиеся строковые поля выносятся в словари, а сама строка становится
    массивом индексов и чисел: имена ключей перестают повторяться в каждой записи.
    `company` в словарь не идёт — уникальных названий почти столько же, сколько
    строк, и словарь там только добавил бы индексы.

    `needk` кодируется индексом в списке значений: их всего три-четыре, зато
    исчезает риск, что 1.2 приедет в браузер с другим хвостом float.

    ФИО закреплённого сотрудника (`emp`) идёт именно СЛОВАРЁМ: сотрудников тысячи,
    а строк на проме десятки тысяч — повтор имени в каждой строке раздул бы файл.
    """
    import json

    def js(v):
        return json.dumps(v, ensure_ascii=False).replace("</", "<\\/")

    if not rows:
        return "[]"
    dicts = {c: [] for c in _PACK_DICT}
    idx = {c: {} for c in _PACK_DICT}
    for c in _PACK_DICT:
        for r in rows:
            v = r.get(c) or ""
            if v not in idx[c]:
                idx[c][v] = len(dicts[c])
                dicts[c].append(v)
    ks = sorted({float(r.get("needk") or 0.0) for r in rows})
    kidx = {v: i for i, v in enumerate(ks)}
    packed = [[r["inn"], r.get("company") or "", idx["lever"][r.get("lever") or ""],
               idx["gosb"][r.get("gosb") or ""], idx["seg"][r.get("seg") or ""],
               r["fl"], r["fot"], idx["reason"][r.get("reason") or ""],
               idx["action"][r.get("action") or ""],
               kidx[float(r.get("needk") or 0.0)],
               idx["emp"][r.get("emp") or ""]] for r in rows]
    d = ",".join(f'{c[0]}:{js(dicts[c])}' for c in _PACK_DICT)
    return (f'(function(){{var D={{{d}}},K={js(ks)},R={js(packed)};'
            f'return R.map(function(x){{return {{inn:x[0],company:x[1],'
            f'lever:D.l[x[2]],gosb:D.g[x[3]],seg:D.s[x[4]],fl:x[5],fot:x[6],'
            f'reason:D.r[x[7]],action:D.a[x[8]],needk:K[x[9]],'
            f'emp:D.e[x[10]]}};}});}})()')


def orgs_explorer(table_id: str, rows: list[dict], gosb_options: list[str],
                  seg_options: list[str] | None = None, page_size: int = 15,
                  all_label: str = "Все организации") -> str:
    """Интерактивная таблица организаций: цель по плану + поиск + фильтры (ГОСБ,
    сегмент, рычаг) + пагинация. Самодостаточный инлайн-JS (работает офлайн).

    ФИЛЬТРЫ ИЗМЕРЕНИЙ ЖИВУТ В ШАПКЕ ТАБЛИЦЫ, а не отдельной строкой над ней.
    Раньше над таблицей стояли «Все ГОСБ», «Все сегменты», «Все рычаги», а прямо
    под ними — колонки «ГОСБ», «Сегмент», «Рычаг»: два ряда подписей об одном и
    том же. Теперь название колонки и есть подпись её фильтра. Наверху остаётся
    только то, что колонкой не является: цель по плану, поиск и выгрузка.

    rows: [{inn, lever, gosb, seg, emp, fl, fot, reason, action, needk}, ...]
    emp — ФИО закреплённого за организацией сотрудника (пусто → прочерк).
    Фильтр «Цель»: needk — минимальная цель (1.0/1.2/1.5), при которой организация
    нужна для закрытия разрыва её сегмента; 0 — не нужна ни при какой (видна только
    при выборе «все с эффектом от порога»).

    Строки уезжают в страницу СЛОВАРНОЙ УПАКОВКОЙ, а не списком JSON-объектов: на
    проме их десятки тысяч, и объектная запись раздувала файл до 20 МБ. В каждой
    строке повторялись имена ключей (треть объёма) и одни и те же значения —
    название ГОСБ переписывалось целиком, хотя ГОСБ полтора десятка. Замер: −60%.
    JS распаковывает payload обратно в ТЕ ЖЕ объекты, поэтому фильтрация, поиск и
    отрисовка ниже не знают об упаковке вовсе.
    """
    data = _pack(rows)
    gopts = "".join(f'<option value="{esc(g)}">{esc(g)}</option>' for g in gosb_options)
    sopts = "".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in (seg_options or []))
    tid = esc(table_id)
    return f"""
<div class="filters">
  <label class="flt-lab">Цель
    <select id="{tid}-k">
      <option value="1">Выполнить план</option>
      <option value="1.2">Перевыполнить на 20%</option>
      <option value="1.5">Перевыполнить на 50%</option>
      <option value="0">{esc(all_label)}</option>
    </select>
  </label>
  <input id="{tid}-q" placeholder="Поиск: номер, название, ГОСБ, сотрудник, сегмент, причина…">
  <span class="xls-slot" id="{tid}-xls"></span>
</div>
<div class="tbl-scroll"><table id="{tid}-t" data-xls-into="{tid}-xls">
  <thead><tr>
    <th>Организация</th>
    <th class="flt-col">Рычаг<select class="tbl-flt" id="{tid}-l" aria-label="Фильтр по рычагу">
      <option value="">все</option>
      <option value="Привлечь">Привлечь</option>
      <option value="Вернуть">Вернуть</option></select></th>
    <th class="flt-col">ГОСБ<select class="tbl-flt" id="{tid}-g" aria-label="Фильтр по ГОСБ">
      <option value="">все</option>{gopts}</select></th>
    <th>Сотрудник</th>
    <th class="flt-col">Сегмент<select class="tbl-flt" id="{tid}-s" aria-label="Фильтр по сегменту">
      <option value="">все</option>{sopts}</select></th>
    <th class="num">Эффект, чел</th><th class="num">ФОТ, млн</th><th>Причина / действие</th>
  </tr></thead><tbody></tbody>
</table></div>
<div class="pager">
  <div class="info" id="{tid}-i"></div>
  <div class="btns"><button id="{tid}-p">← Назад</button><button id="{tid}-n">Вперёд →</button></div>
</div>
<script>
(function(){{
  const DATA={data};
  const PS={page_size};
  let page=0;
  const $=id=>document.getElementById("{tid}-"+id);
  const fmt=n=>Number(n).toLocaleString('ru-RU',{{maximumFractionDigits:0}});
  function filtered(){{
    const q=($('q').value||'').toLowerCase(), g=$('g').value, s=$('s').value, l=$('l').value;
    const k=parseFloat($('k').value);
    return DATA.filter(r=>{{
      // цель по плану: организация нужна, если её needk не больше выбранной цели
      if(k>0&&!(Number(r.needk)>0&&Number(r.needk)<=k))return false;
      if(g&&r.gosb!==g)return false;
      if(s&&r.seg!==s)return false;
      if(l&&r.lever!==l)return false;
      if(q){{const hay=(r.inn+' '+(r.company||'')+' '+r.gosb+' '+(r.emp||'')+' '+r.seg+' '+r.reason).toLowerCase();
        if(!hay.includes(q))return false;}}
      return true;
    }});
  }}
  function render(){{
    const rows=filtered();
    const pages=Math.max(1,Math.ceil(rows.length/PS));
    if(page>=pages)page=pages-1; if(page<0)page=0;
    const slice=rows.slice(page*PS,page*PS+PS);
    const tb=$('t').querySelector('tbody');
    tb.innerHTML=slice.map(r=>{{
      const cls=r.lever==='Привлечь'?'good':'bad';
      const act=r.action?' · <span style="color:var(--text-2)">'+esc(r.action)+'</span>':'';
      return '<tr><td><div>'+esc(r.company||('Орг. '+r.inn))+'</div>'
        +'<div style="font-size:12px;color:var(--text-2)">Орг. '+r.inn+'</div></td>'
        +'<td><span class="badge '+cls+'">'+r.lever+'</span></td>'
        +'<td>'+esc(r.gosb)+'</td><td>'+esc(r.emp||'—')+'</td><td>'+esc(r.seg)+'</td>'
        +'<td class="num">'+fmt(r.fl)+'</td><td class="num">'+fmt(r.fot)+'</td>'
        +'<td>'+esc(r.reason)+act+'</td></tr>';
    }}).join('')||'<tr><td colspan="8" style="color:var(--text-2)">Ничего не найдено</td></tr>';
    /* какой фильтр включён, видно по самой шапке — не раскрывая список */
    ['g','s','l'].forEach(k=>$(k).classList.toggle('on', !!$(k).value));
    const sumFl=rows.reduce((a,r)=>a+Number(r.fl||0),0);
    $('i').textContent='Показано '+slice.length+' из '+rows.length+' орг · суммарный эффект +'
      +fmt(sumFl)+' чел · стр. '+(page+1)+'/'+pages;
    $('p').disabled=page<=0; $('n').disabled=page>=pages-1;
  }}
  function esc(s){{return String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
  ['k','q','g','s','l'].forEach(k=>$(k).addEventListener('input',()=>{{page=0;render();}}));
  $('p').addEventListener('click',()=>{{page--;render();}});
  $('n').addEventListener('click',()=>{{page++;render();}});
  /* Выгрузка в Excel отдаёт ВСЕ отобранные строки, а не видимую страницу:
     на экране их пятнадцать, а работать идут со всем списком фильтра. */
  window.__xlsRows = window.__xlsRows || {{}};
  window.__xlsRows["{tid}-t"] = function(){{
    var out=[["Организация","Орг. N","Рычаг","ГОСБ","Сотрудник","Сегмент",
              "Эффект, чел","ФОТ, млн","Причина","Действие"]];
    filtered().forEach(function(r){{
      out.push([r.company||('Орг. '+r.inn), r.inn, r.lever, r.gosb, r.emp||'',
                r.seg, r.fl, r.fot, r.reason, r.action||'']);
    }});
    return out;
  }};
  render();
}})();
</script>
"""
