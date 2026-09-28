"""Сборка дэша «Работа с оттоком» — второй отчёт того же семейства.

Задача та же, что у tb_health: один файл без сети, уровни СБ → ТБ → ГОСБ,
читает топ-менеджмент. Разница в предмете: здесь не «выполним ли план», а «кого
мы уже потеряли, становится ли хуже и чем закончилась работа по потерям».

ЧТО ПЕРЕИСПОЛЬЗУЕТСЯ У ПЕРВОГО ОТЧЁТА. Всё оформление (стили дизайнера, экран
выбора раздела, выгрузка таблиц в Excel, переключение уровней, оверлеи) и
расчёт групп работы по оттоку. Копировать это во второй отчёт нельзя: два
экземпляра одной логики разъезжаются на первой же правке, и числа по одному и
тому же месяцу перестают совпадать. Поэтому модуль импортирует куски первого
отчёта напрямую — связь односторонняя: tb_health про этот отчёт не знает.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from ...registry import Context, dashboard
from ...render import components as C
from ...render import page
from ... import progress
from ..tb_health import analyze as th
from ..tb_health import bank, view as base
from . import analyze

TITLE = "Работа с оттоком получателей заработной платы"
SUBTITLE = "Кого потеряли, вернули ли и что с этим делали"


@dashboard("outflow")
def build(ctx: Context) -> str:
    """Отчёт по оттоку: банк целиком и по вкладке на каждый ТБ."""
    b = bank.load(ctx)
    hist = analyze.load_history(ctx, b)

    levels_tb = sorted(((int(r.tb_id), str(r.tb_short_name), str(r.tb_full_name))
                        for r in b.tbs.itertuples()), key=lambda x: th._ru_key(x[1]))
    progress.step("Уровень СБ: отток по всему банку")
    sb = analyze.prepare(b, hist, None, "СБ", "Сбербанк — все территориальные банки")
    levels = [sb]
    for i, (tb_id, short, full) in enumerate(levels_tb, start=1):
        progress.step(f"═══ ТБ {short} ({i} из {len(levels_tb)}) ═══")
        levels.append(analyze.prepare(b, hist, tb_id, short, full))
    _log(sb)

    # карточка ТБ на главном экране знает номер вкладки своего разбора
    lvl_of = {l.tb_id: i for i, l in enumerate(levels) if l.tb_id is not None}

    progress.step("Сборка HTML")
    bodies = "".join(
        f'<div class="lvl" id="lvl-{i}"{"" if i == 0 else " hidden"}>'
        f'{_level_body(lvl, b, i, lvl_of)}</div>' for i, lvl in enumerate(levels))
    d = b.dates or {}
    meta = (f'Окно отчёта — {C.esc(d.get("out_label", ""))} · '
            f'последний закрытый месяц {C.esc(d.get("closed_label", ""))} · '
            f'отчёт обновлён {datetime.now():%d.%m.%Y, %H:%M}')
    return page(
        title=TITLE, subtitle=SUBTITLE, meta=meta,
        body=(base._BOOT_JS + base._LEGEND + _tabs(levels) + bodies
              + base._GD_JS + base._LVL_JS + base._OF_JS),
        css=base._asset("ux.css") + base._asset("ux-fix.css"),
        tail=(f'<script>{base._asset("ux.js.txt")}</script>\n'
              f'{base._HUB_JS}\n{base._REVEAL_JS}\n{base._XLS_JS}\n'
              f'<div id="print-root" aria-hidden="true"></div>'),
    )


def _log(sb: analyze.Level) -> None:
    t = sb.totals or {}
    if not t:
        return
    progress.done(
        f"Отток банка за окно: ушло {t['gone']:,.0f} чел по {t['orgs']:,} строкам, "
        f"вернулось {t['ret']:,.0f} ({(t.get('ret_share') or 0) * 100:.0f}%), "
        f"безвозвратно {t['kept']:,.0f} чел"
        .replace(",", " "))
    if t.get("delta_kept") is not None:
        w = "больше" if t["delta_kept"] > 0 else "меньше"
        progress.done(f"К предыдущим трём месяцам потеряли на "
                      f"{abs(t['delta_kept']):,.0f} чел {w}".replace(",", " "))


# --------------------------------------------------------------------------- #
def _tabs(levels: list) -> str:
    btns = "".join(
        f'<button class="lvl-tab{" on" if i == 0 else ""}" data-lvl="{i}" '
        f'onclick="lvlGo({i})">{C.esc(l.short)}</button>'
        for i, l in enumerate(levels))
    return f'<nav class="lvls">{btns}</nav>'


def _level_body(lvl: analyze.Level, b, idx: int, lvl_of: dict | None = None) -> str:
    """Уровень целиком: вердикт, показатели, плашки разделов и сами разделы."""
    secs = [("trend", _trend_section(lvl)),
            ("matrix", _matrix_section(lvl, idx)),
            ("units", _units_section(lvl, idx, lvl_of)),
            ("reasons", _reasons_section(lvl)),
            ("work", _work_section(lvl, b)),
            ("orgs", _orgs_section(lvl))]
    secs = [(k, h) for k, h in secs if h]
    return (_head(lvl, idx) + _hero(lvl, b) + _kpis(lvl)
            + _hub(lvl, idx, secs) + _reco(lvl)
            + "".join(base._with_id(_with_reco(h, lvl.reco_sec.get(k)),
                                    f"sec-{idx}-{k}") for k, h in secs)
            + base._contacts())


def _head(lvl: analyze.Level, idx: int) -> str:
    back = ('<button type="button" class="lvl-up" onclick="lvlGo(0)">← Все банки'
            '</button>' if idx else "")
    return ('<div class="lvl-head">'
            f'<div class="lvl-id">{back}<h2 class="lvl-name">{C.esc(lvl.full)}</h2></div>'
            f'<button type="button" class="lvl-pdf" onclick="exportLevelPdf({idx})" '
            f'title="Собрать отчёт этого уровня в PDF">Скачать PDF</button></div>')


def _status(delta) -> str:
    """Статус уровня — по динамике потерь, а не по нормативу.

    Плана по оттоку в витрине нет, и выдумывать порог «сколько терять можно»
    отчёт не вправе. Зато рост потерь к предыдущему такому же периоду — факт из
    тех же данных, и именно он задаёт цвет.
    """
    if delta is None:
        return "warn"
    if delta < -0.5:
        return "good"
    return "bad" if delta > 0.5 else "warn"


def _hero(lvl: analyze.Level, b) -> str:
    t = lvl.totals or {}
    if not t:
        return C.card('<div class="verdict">Данных по оттоку за окно нет</div>',
                      cls="hero")
    d = b.dates or {}
    st = _status(t.get("delta_kept"))
    word = {"good": "Потери снижаются", "warn": "Потери держатся на прежнем уровне",
            "bad": "Потери растут"}[st]
    ret_pct = (t.get("ret_share") or 0) * 100
    delta = t.get("delta_kept")
    if delta is None:
        dyn = "сравнивать не с чем: истории за предыдущий период в витрине нет"
    else:
        col = "var(--bad)" if delta > 0 else "var(--good)"
        sign = "+" if delta > 0 else "−"
        prev = ", ".join(_m(x) for x in (t.get("prev_months") or []))
        dyn = (f'к предыдущим трём месяцам ({C.esc(prev)}): безвозвратные потери '
               f'<b style="color:{col}">{sign}{C.fmt_num(abs(delta))} чел</b>')
    inner = (
        f'<div class="eyebrow">Отток за {C.esc(d.get("out_label", ""))}</div>'
        f'<div class="verdict">{C.esc(word)}</div>'
        f'<div class="row2 row2-fot">Ушло <b>{C.fmt_num(t["gone"])}</b> чел · '
        f'вернулось <b>{C.fmt_num(t["ret"])}</b> ({ret_pct:.0f}%) · '
        f'безвозвратные потери <b style="color:var(--bad)">{C.fmt_num(t["kept"])}</b> чел'
        f'</div>'
        f'<div class="row2">{dyn}</div>')
    return C.card(inner, cls=f"hero {st}")


def _kpis(lvl: analyze.Level) -> str:
    """Две карточки показателей — их ждёт скрипт дизайна сразу за плашкой."""
    t = lvl.totals or {}
    if not t:
        return '<div class="grid cols-2"></div>'
    ret_pct = (t.get("ret_share") or 0) * 100
    prev = t.get("prev") or {}
    prev_pct = (prev.get("ret_share") or 0) * 100 if prev else None
    foot_ret = (f'<div class="foot">в предыдущем периоде {prev_pct:.0f}%</div>'
                if prev_pct is not None else "")
    kept_foot = (f'<div class="foot">в предыдущем периоде '
                 f'{C.fmt_num(prev.get("kept", 0))} чел</div>' if prev else "")
    force = t.get("force") or 0
    cards = (
        C.card(f'<div class="label">Безвозвратные потери, чел</div>'
               f'<div class="value">{C.fmt_num(t["kept"])}</div>'
               f'<div class="delta">ушло {C.fmt_num(t["gone"])} · '
               f'вернулось {C.fmt_num(t["ret"])}</div>{kept_foot}', cls="kpi")
        + C.card(f'<div class="label">Доля возврата</div>'
                 f'<div class="value">{ret_pct:.0f}%</div>'
                 f'<div class="delta">вынужденный отток '
                 f'{C.fmt_num(force)} чел</div>{foot_ret}', cls="kpi"))
    return f'<div class="grid cols-2">{cards}</div>'


def _m(dt) -> str:
    ts = pd.Timestamp(dt)
    return f"{ts.month:02d}.{ts.year}"


def _with_reco(html: str, items: list | None) -> str:
    """Дописать в конец раздела его собственные рекомендации.

    Именно в конец: сначала читатель видит числа, потом — что с ними делать.
    Вставка идёт в готовую разметку раздела, чтобы каждая функция раздела
    продолжала отвечать только за свои данные.
    """
    if not html or not items:
        return html
    return html.replace("</section>", _reco_card(
        items, "Что делать с этим разделом", "reco-sec") + "</section>", 1)


def _reco_card(items: list, title: str, cls: str = "") -> str:
    """Карточка рекомендаций: действие и под ним основание числами."""
    li = "".join(
        f'<li class="reco-i {C.esc(r.get("kind", ""))}">'
        f'<span class="reco-do">{C.esc(r["do"])}</span>'
        f'<span class="reco-why">{C.esc(r["why"])}</span></li>' for r in items)
    return (f'<div class="card reco {cls}"><div class="eyebrow">Рекомендации</div>'
            f'<h3 style="margin:2px 0 10px">{C.esc(title)}</h3>'
            f'<ol class="reco-list">{li}</ol></div>')


def _reco(lvl: analyze.Level) -> str:
    """Общий блок «Что делать» — на главном экране, под плашками разделов.

    Порядок экрана: вердикт → показатели → из чего состоит отчёт → что делать.
    Рекомендации читают после того, как увидели картину целиком, и тогда же
    решают, в какой раздел идти; отдельным разделом их прятать нельзя — за
    кликом их увидела бы половина читателей.

    Каждый пункт — действие и под ним основание числами. Без основания пункт
    не собирается вовсе (см. analyze._reco): рекомендация без цифры в
    управленческом отчёте выглядит как общее пожелание.
    """
    if not lvl.reco:
        return ""
    note = ('<p class="reco-note">Пункты собраны по числам этого уровня: красные '
            'зоны, концентрация потерь, сегмент, результат работы по ушедшим '
            'клиентам и причина ухода. Порядок — по весу в потерях. В каждом '
            'разделе есть свои рекомендации — по тому, что в нём показано.</p>')
    card = _reco_card(lvl.reco, "Что делать")
    return card.replace("</ol></div>", "</ol>" + note + "</div>")


# --------------------------------------------------------------------------- #
def _hub(lvl: analyze.Level, idx: int, secs: list) -> str:
    """Экран выбора раздела — тот же, что в первом отчёте: плашки вместо свитка."""
    facts = _hub_facts(lvl)
    tiles = []
    for key, html in secs:
        lead, nums = facts.get(key, ("", []))
        title = base._sec_title(html)
        if not title:
            continue
        rows = "".join(
            f'<span class="hub-n"><b{f" class=\"{st}\"" if st else ""}>{val}</b>'
            f'<i>{C.esc(lbl)}</i></span>' for val, lbl, st in nums)
        tiles.append(
            f'<button type="button" class="hub-tile" data-sec="sec-{idx}-{key}">'
            f'<span class="hub-h">{title}<i>→</i></span>'
            f'<span class="hub-d">{C.esc(lead)}</span>'
            f'<span class="hub-nums">{rows}</span></button>')
    if not tiles:
        return ""
    t = lvl.totals or {}
    back = ('<div class="hub-back">'
            '<button type="button" class="hub-home">← Все разделы</button>'
            '<span class="hub-back-t"></span>'
            f'<span class="hub-back-fc">безвозвратные потери: '
            f'<b style="color:var(--bad)">{C.fmt_num(t.get("kept", 0))}</b> чел</span>'
            '</div>')
    return (f'<div class="hub-wrap"><div class="hub-lead">Разделы отчёта — '
            f'откройте нужный, страница покажет только его</div>'
            f'<div class="hub" data-n="{len(tiles)}">{"".join(tiles)}'
            f'</div></div>{back}')


def _hub_facts(lvl: analyze.Level) -> dict:
    """Что написано на плашке раздела: строка «что внутри» и главные числа."""
    unit = lvl.unit_label
    out: dict = {}
    if lvl.months:
        worst = max(lvl.months, key=lambda x: x["kept"])
        out["trend"] = ("Как потери и возвраты шли месяц за месяцем.",
                        [(f'{len(lvl.months)}', "закрытых месяцев в графике", ""),
                         (C.fmt_num(worst["kept"]), f'худший месяц — {worst["label"]}',
                          "bad")])
    if not lvl.matrix.empty:
        n_units = lvl.matrix["unit_id"].nunique()
        out["matrix"] = (f"Где теряем: каждый {unit} в разрезе сегментов.",
                         [(C.fmt_num(lvl.matrix["kept"].sum()), "чел потерь в матрице",
                           "bad"),
                          (f'{n_units} × {len(lvl.segs)}', f'{unit} и сегментов', "")])
    if lvl.units:
        worst = lvl.units[0]
        out["units"] = (f"Карточка на каждый {unit}: потери, возврат, динамика.",
                        [(C.fmt_num(len(lvl.units)), f'{unit} в разборе', ""),
                         (C.fmt_num(worst["kept"]), f'худший — {worst["name"][:22]}',
                          "bad")])
    o = lvl.outflow or {}
    if o.get("reasons"):
        top = o["reasons"][0]
        # название причины — в ПОДПИСИ, а не в значении: значение не переносится
        # по словам, и длинная причина вылезала за край плашки
        out["reasons"] = ("Из-за чего уходят: свод по причинам из задач.",
                          [(C.fmt_num(len(o["reasons"])), "причин в своде", ""),
                           (C.fmt_num(top.get("kept", 0)),
                            f'чел по главной причине — {str(top.get("reason", ""))}',
                            "bad")])
    if o.get("top_promised") or o.get("top_worked"):
        out["work"] = ("Чем закончилась работа по ушедшим клиентам.",
                       [(C.fmt_num(len(o.get("top_promised") or [])),
                         "обещали вернуться, но не вернулись", ""),
                        (C.fmt_num(len(o.get("top_worked") or [])),
                         "отработали без результата", "")])
    if lvl.orgs:
        out["orgs"] = ("Поимённый список крупнейших потерь с выгрузкой.",
                       [(C.fmt_num(len(lvl.orgs)), "организаций в списке", ""),
                        (C.fmt_num(sum(r["kept"] for r in lvl.orgs)),
                         "чел в них потеряли", "bad")])
    return out


# --------------------------------------------------------------------------- #
def _trend_section(lvl: analyze.Level) -> str:
    """Динамика потерь и возвратов по месяцам: график и те же числа таблицей."""
    if not lvl.months:
        return ""
    m = lvl.months
    first, last = m[0], m[-1]
    worst = max(m, key=lambda x: x["kept"])
    lead = (f'<p class="sub" style="font-size:15px;margin:-2px 0 14px">'
            f'Период {C.esc(first["label"])} — {C.esc(last["label"])}, закрытые '
            f'месяцы. Худший месяц — <b>{C.esc(worst["label"])}</b>: безвозвратно '
            f'{C.fmt_num(worst["kept"])} чел. В последнем закрытом месяце '
            f'{C.fmt_num(last["kept"])} чел.</p>')
    rows = [[r["label"], C.fmt_num(r["gone"]), C.fmt_num(r["ret"]),
             f'<b style="color:var(--bad)">−{C.fmt_num(r["kept"])}</b>',
             f'{(r["ret"] / r["gone"] * 100) if r["gone"] else 0:.0f}%']
            for r in m]
    tbl = C.table(["Месяц", "ушло, чел", "вернулось, чел", "потери, чел",
                   "доля возврата"], rows, num_cols=[1, 2, 3, 4])
    return C.section("Динамика оттока за 12 месяцев",
                     lead + C.card(_bars(m) + tbl),
                     eyebrow="Ретроспектива потерь",
                     desc="Сколько людей уходило и сколько возвращалось месяц за "
                          "месяцем — чтобы отличить разовый уход крупного клиента "
                          "от устойчивого роста потерь.")


def _bars(months: list) -> str:
    """Столбики потерь по месяцам — инлайновый SVG, без библиотек.

    Рисуем именно безвозвратные потери: возврат показан отдельной линией снизу
    в подписи, а сравнивать между месяцами нужно то, что мы потеряли насовсем.
    """
    if not months:
        return ""
    w, h, pad = 980, 190, 26
    top = max((x["kept"] for x in months), default=0) or 1
    bw = (w - pad * 2) / max(len(months), 1)
    bars, labels = [], []
    for i, r in enumerate(months):
        bh = (r["kept"] / top) * (h - pad * 2)
        x = pad + i * bw + bw * 0.18
        y = h - pad - bh
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.64:.1f}" '
                    f'height="{max(bh, 1):.1f}" rx="3" fill="var(--bad)" '
                    f'opacity="0.82"><title>{C.esc(r["label"])}: потери '
                    f'{C.fmt_num(r["kept"])} чел, вернулось {C.fmt_num(r["ret"])}'
                    f'</title></rect>')
        labels.append(f'<text x="{pad + i * bw + bw / 2:.1f}" y="{h - 8}" '
                      f'font-size="10" text-anchor="middle" '
                      f'fill="var(--text-2)">{C.esc(r["short"])}</text>')
        labels.append(f'<text x="{pad + i * bw + bw / 2:.1f}" y="{y - 5:.1f}" '
                      f'font-size="10.5" text-anchor="middle" '
                      f'fill="var(--text)">{C.fmt_num(r["kept"])}</text>')
    return (f'<div class="ch-cap">Безвозвратные потери помесячно, человек</div>'
            f'<div class="chart"><svg viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="xMidYMid meet" role="img" '
            f'aria-label="Потери по месяцам">{"".join(bars)}{"".join(labels)}'
            f'</svg></div>')


# --------------------------------------------------------------------------- #
def _matrix_section(lvl: analyze.Level, idx: int) -> str:
    """Матрица «единица × сегмент» по безвозвратным потерям.

    Цвет здесь НЕ статус выполнения плана (его у оттока нет), а вес ячейки:
    чем темнее, тем большую часть потерь уровня даёт эта пара. Поэтому шкала
    своя, а не общая с первым отчётом, — иначе один и тот же красный означал бы
    в двух отчётах разное.
    """
    m = lvl.matrix
    if m.empty or not lvl.segs:
        return ""
    units = (m.groupby(["unit_id", "unit_name"])["kept"].sum()
              .reset_index().sort_values("kept", ascending=False))
    cells = {(int(r.unit_id), r.seg_name): float(r.kept) for r in m.itertuples()}
    top = max(cells.values(), default=0) or 1
    head = ("<th>" + C.esc(lvl.unit_label) + "</th>"
            + "".join(f'<th class="num">{C.esc(s)}</th>' for s in lvl.segs)
            + '<th class="num">всего</th>')
    rows = []
    for r in units.itertuples():
        uid, name = int(r.unit_id), str(r.unit_name)
        tds = [f'<td>{C.esc(name[:26])}</td>']
        for s in lvl.segs:
            v = cells.get((uid, s))
            if not v:
                tds.append('<td class="num" style="color:var(--text-2)">—</td>')
                continue
            share = v / top
            bg = f"rgba(255,59,48,{0.08 + share * 0.55:.3f})"
            tds.append(f'<td class="num heat" style="background:{bg}" '
                       f'title="{C.esc(name)} · {C.esc(s)}: потеряли '
                       f'{C.fmt_num(v)} чел">{C.fmt_num(v)}</td>')
        tds.append(f'<td class="num"><b>{C.fmt_num(r.kept)}</b></td>')
        rows.append(f'<tr>{"".join(tds)}</tr>')
    tbl = (f'<div style="overflow-x:auto"><table class="matrix"><thead><tr>{head}'
           f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    hint = ('<p class="sub" style="font-size:14px;margin:-4px 0 12px">'
            'В ячейке — сколько человек потеряли безвозвратно за окно отчёта. '
            'Чем темнее заливка, тем большую часть потерь уровня даёт эта пара; '
            'сегмент берётся из витрины оттока, а не из справочника клиентов.</p>')
    return C.section(f"Матрица потерь {lvl.unit_label}/сегмент",
                     C.card('<h3>Безвозвратные потери, чел</h3>' + hint + tbl),
                     eyebrow="Где именно теряем",
                     desc="Потери в разрезе подразделений и сегментов — чтобы "
                          "увидеть, где сосредоточен отток, прежде чем идти в "
                          "детали по клиентам.")


def _units_section(lvl: analyze.Level, idx: int, lvl_of: dict | None = None) -> str:
    """Карточки единиц: потери, возврат и куда двигается каждая единица.

    На уровне банка карточка ТБ ведёт в его собственный разбор — это и есть
    переход СБ → ТБ, тот же, что в первом отчёте.
    """
    if not lvl.units:
        return ""
    cards = []
    for u in lvl.units:
        delta = u.get("delta")
        st = _status(delta)
        if delta is None:
            dyn = '<div class="g-act">сравнить не с чем: истории за предыдущий период нет</div>'
        else:
            col = "var(--bad)" if delta > 0 else "var(--good)"
            sign = "+" if delta > 0 else "−"
            dyn = (f'<div class="g-act">к предыдущим трём месяцам '
                   f'<b style="color:{col}">{sign}{C.fmt_num(abs(delta))} чел</b> '
                   f'(было {C.fmt_num(u["was_kept"])})</div>')
        ret_pct = (u.get("ret_share") or 0) * 100
        inner = (
            f'<div class="g-head"><h3 style="margin:0">{C.esc(u["name"])}</h3>'
            f'<span class="g-ex {st}"><i>потери</i>{C.fmt_num(u["kept"])}</span></div>'
            f'<div class="g-fc">ушло {C.fmt_num(u["gone"])} · вернулось '
            f'{C.fmt_num(u["ret"])} ({ret_pct:.0f}%)</div>'
            f'<div style="margin:2px 0 6px">'
            + C.badge(f'{u["orgs"]} строк оттока за окно', "warn") + '</div>'
            + dyn)
        go = (lvl_of or {}).get(u["id"])
        drill = (f'<div class="g-more g-drill" onclick="event.stopPropagation();'
                 f'lvlGo({go})">Открыть разбор {C.esc(u["name"])} →</div>'
                 if go else "")
        cards.append(f'<div class="card gcard {st}">{inner}{drill}</div>')
    grid = f'<div class="gcards">{"".join(cards)}</div>'
    return C.section(f"Детализация по {lvl.unit_label}",
                     grid, eyebrow=f"Детализация по {lvl.unit_label}",
                     desc="Потери каждого подразделения за окно отчёта и куда "
                          "они сдвинулись к предыдущим трём месяцам.")


def _reasons_section(lvl: analyze.Level) -> str:
    """Свод причин ухода — тем же расчётом и теми же словами, что в первом отчёте."""
    o = lvl.outflow or {}
    if not o.get("reasons"):
        return ""
    body = base._outflow_reasons(o)
    if not body:
        return ""
    return C.section("Причины оттока", C.card(body),
                     eyebrow="Из-за чего уходят",
                     desc="Причина берётся из чек-листа задачи по оттоку, а если "
                          "он не заполнен — распознаётся по формулировке в "
                          "комментарии сотрудника.")


def _work_section(lvl: analyze.Level, b) -> str:
    """Две группы работы по оттоку — расчёт первого отчёта, разметка его же."""
    o = lvl.outflow or {}
    groups = []
    cur_label = (b.dates or {}).get("label", "")
    for rows_src, title, lead, kind in (
        (o.get("top_promised") or [], "Обещали вернуться, но не вернулись",
         "Договорённость о возврате зафиксирована в задаче по оттоку, а люди "
         "не вернулись. Предмет разговора — и с клиентом, и с исполнителем.", "promise"),
        (o.get("top_worked") or [], "Отработали, но без результата",
         "Работа в месяцы ухода велась, возврат никто не обещал, и не вернулся "
         "никто. Вопрос к подходу, а не к исполнению договорённости.", "worked"),
    ):
        if not rows_src:
            continue
        n, kept = len(rows_src), sum(r["kept"] for r in rows_src)
        groups.append({"title": title,
                       "sub": (f'{n} {base._plural(n, "клиент", "клиента", "клиентов")} · '
                               f'потери {C.fmt_num(kept)} чел'),
                       "html": base._outflow_group(rows_src, lvl.unit_label, title,
                                                   lead, kind, cur_label)})
    if not groups:
        return ""
    return C.section("Результат работы по оттоку",
                     C.card(base._outflow_tabs(groups)),
                     eyebrow="Чем закончилась работа",
                     desc="Потери, по которым работа велась: отдельно "
                          "невыполненные обещания вернуть получателей, отдельно "
                          "отработка, которая результата не дала.")


def _orgs_section(lvl: analyze.Level) -> str:
    """Крупнейшие потери поимённо — одной таблицей, с выгрузкой в Excel."""
    if not lvl.orgs:
        return ""
    rows = []
    for r in lvl.orgs:
        who = C.esc(r.get("emp") or "")
        sub = f'<div class="gd-emp">{who}</div>' if who else ""
        rows.append([
            f'{C.esc(r["name"])}{sub}',
            C.esc(r.get("unit", "")),
            C.fmt_num(r.get("gone", 0)),
            C.fmt_num(r.get("ret", 0)) if r.get("ret") else "—",
            f'<b style="color:var(--bad)">−{C.fmt_num(r["kept"])}</b>',
            C.esc(", ".join(r.get("months") or [])),
            C.esc(r.get("out_reason") or "причина не зафиксирована"),
            C.esc(r.get("group", "")),
        ])
    tbl = C.table(["Организация · ответственный", lvl.unit_label, "ушло, чел",
                   "вернулось, чел", "потери, чел", "период оттока",
                   "причина ухода", "результат работы"],
                  rows, num_cols=[2, 3, 4])
    lead = (f'<p class="sub" style="font-size:14px;margin:-4px 0 10px">'
            f'{len(rows)} {base._plural(len(rows), "организация", "организации", "организаций")} '
            f'с наибольшими безвозвратными потерями за окно отчёта.</p>')
    return C.section("Крупнейшие потери",
                     C.card('<h3>Кого потеряли</h3>' + lead + tbl),
                     eyebrow="Поимённо",
                     desc="Список клиентов с наибольшими потерями: сколько ушло, "
                          "сколько вернулось, из-за чего ушли и чем закончилась "
                          "работа по ним.")
