"""Обзор портфеля в оформлении из пользовательского HTML-референса.

Использует готовые показатели анализа; расчёты прогноза и отбора остаются в analyze.
"""
from __future__ import annotations

import re

from ...render import components as C
from .freshness import orgs_snapshot

_MONTHS = ("янв", "фев", "мар", "апр", "май", "июн",
           "июл", "авг", "сен", "окт", "ноя", "дек")


def _tone(value):
    return "" if value is None else ("positive" if value >= 0 else "negative")


def _signed(value):
    return ("+" if value >= 0 else "−") + C.fmt_num(abs(value))


def _link(idx, section, label, titles):
    if not titles.get(section):
        return ""
    return (f'<button type="button" class="pd-link" data-pd-section="sec-{idx}-{section}">'
            f'{C.esc(label)}<span aria-hidden="true">↗</span></button>')


def _metric(title, row, unit, scale=1):
    fact, plan = float(row.get("fact") or 0) / scale, float(row.get("plan") or 0) / scale
    execution = row.get("exec")
    status = ("" if execution is None else "positive" if execution >= 1 else
              "near" if execution >= .95 else "negative")
    percent = "—" if execution is None else f'{execution * 100:.1f}'.replace(".", ",") + "%"
    return (
        '<article class="pd-metric pd-metric--forecast"><div class="pd-metric-top">'
        f'<h3>{C.esc(title)}</h3></div><div class="pd-forecast-label">Прогноз</div>'
        f'<div class="pd-number">{C.fmt_num(fact)}<small>{C.esc(unit)}</small></div>'
        '<dl class="pd-metric-details">'
        f'<div><dt>План</dt><dd>{C.fmt_num(plan)} <small>{C.esc(unit)}</small></dd></div>'
        f'<div><dt>Дельта к плану</dt><dd class="{_tone(fact - plan)}">'
        f'{_signed(fact - plan)} <small>{C.esc(unit)}</small></dd></div>'
        '<div class="pd-execution"><dt>Выполнение плана<small>прогноз / план</small></dt>'
        f'<dd class="pd-status {status}">{percent}</dd></div></dl></article>')


def _compact(value):
    if abs(value) >= 1e6:
        return f'{value / 1e6:.2f}'.replace(".", ",") + " млн"
    if abs(value) >= 1e3:
        return f'{value / 1e3:.1f}'.replace(".", ",") + " тыс."
    return C.fmt_num(value)


def _chart(rows):
    if not rows:
        return '<p class="pd-empty">Истории портфеля нет.</p>'
    values = [float(r["fact"]) for r in rows]
    low, high = min(values), max(values)
    pad = (high - low) * .16 or max(abs(high) * .02, 1)
    low, high = max(0, low - pad), high + pad
    points = [(60 + 510 * i / max(1, len(rows) - 1),
               140 - 115 * (v - low) / (high - low)) for i, v in enumerate(values)]
    line = " ".join(f'{x:.2f},{y:.2f}' for x, y in points)
    grid = []
    ticks = ((min(values), (min(values) + max(values)) / 2, max(values))
             if min(values) != max(values) else (low, (low + high) / 2, high))
    for value in ticks:
        y = 140 - 115 * (value - low) / (high - low)
        grid.append(f'<line x1="60" x2="570" y1="{y:.2f}" y2="{y:.2f}"/>'
                    f'<text x="0" y="{y + 4:.2f}">{_compact(value)}</text>')
    dots, labels = [], []
    for i, (row, (x, y)) in enumerate(zip(rows, points)):
        tip = C.esc(f'{row["label"]}: {C.fmt_num(row["fact"])} человек')
        dots.append(f'<circle tabindex="0" cx="{x:.2f}" cy="{y:.2f}" r="4" '
                    f'data-pd-point="{tip}" aria-label="{tip}"><title>{tip}</title></circle>')
        if len(rows) <= 12 or i % 2 == 0:
            month = re.match(r'^(0[1-9]|1[0-2])\.\d{4}$', row["label"])
            caption = _MONTHS[int(month[1]) - 1] if month else row.get("short", row["label"])
            labels.append(f'<text x="{x:.2f}" y="166" text-anchor="middle">'
                          f'{C.esc(caption)}</text>')
    return (
        # Низкий и широкий: на главном экране график делит ряд с рейтингом и
        # действиями, и высокий холст ужимался вместе с подписями до нечитаемых
        '<svg viewBox="0 0 590 178" role="img" aria-label="Динамика фактического портфеля">'
        f'<g class="pd-chart-grid">{"".join(grid)}</g>'
        f'<polygon points="{points[0][0]:.2f},140 {line} {points[-1][0]:.2f},140" '
        f'class="pd-chart-area"/><polyline points="{line}" class="pd-chart-line"/>'
        + "".join(dots) + f'<g class="pd-chart-labels">{"".join(labels)}</g></svg>'
        '<div class="pd-chart-readout" aria-live="polite">'
        'Факт за закрытые месяцы. Шкала не от нуля.</div>'
        f'<div class="pd-trend-foot"><span>Начало периода <b>{C.fmt_num(values[0])}</b></span>'
        f'<span>Конец периода <b>{C.fmt_num(values[-1])}</b></span></div>')


def overview(a, idx, titles, roll, concentration, asks, health=""):
    """Первый экран уровня; все действия ведут в существующие разделы и диалоги."""
    execution = a.verdict["rcp"].get("exec")
    state = ("Нет плана для сравнения" if execution is None else
             "План выполняется" if execution >= 1 else "План не выполняется")
    rows = a.trend or []
    yoy = ((a.yoy or {}).get("rcp") or {}).get("pct")
    yoy_text = "—"
    if yoy is not None:
        yoy_text = ("+" if yoy >= 0 else "−") + f'{abs(yoy) * 100:.1f}'.replace(".", ",") + "%"
    period = f'{rows[0]["label"]} — {rows[-1]["label"]}' if rows else ""
    cards = sorted(a.gosb_cards or [], key=lambda c: (c.get("exec") is None, c.get("exec") or 0))
    scale = max(1, max((float(c.get("exec") or 0) for c in cards), default=1)) * 1.02
    ranking = []
    for card in cards:
        ex = card.get("exec")
        value = "—" if ex is None else f'{ex * 100:.0f}%'
        tone = "" if ex is None or ex >= 1 else "near" if ex >= .95 else "negative"
        name = C.esc(card.get("name") or card.get("gosb_name") or card.get("gosb_id"))
        ranking.append(
            f'<button type="button" class="pd-bank-row {tone}" '
            f'data-pd-dialog="{idx}-{C.esc(card["gosb_id"])}" '
            f'aria-label="{name}: {value}. Открыть разбор" title="{name}: {value}">'
            f'<span>{name}</span>'
            f'<span class="pd-bank-track"><i style="width:{max(0, float(ex or 0)) / scale * 100:.3f}%"></i>'
            f'<em style="left:{100 / scale:.3f}%"></em></span><b>{value}</b></button>')
    attention = ""
    if concentration:
        attention = (
            '<div class="pd-attention"><span class="pd-attention-icon" aria-hidden="true">!</span><div>'
            f'<strong>{C.esc(concentration["name"])}: {concentration["exec"] * 100:.0f}% плана</strong>'
            f'<p>Не хватает {C.fmt_num(concentration["gap"])} получателей. '
            'Закрепить ответственного и сроки отработки.</p></div>'
            + _link(idx, "gosb", "Разобрать", titles) + '</div>')
    tasks = []
    for ask in asks:
        anchor = re.search(r'href="#([^"]+)"', ask)
        text = re.sub(r'<a\b.*?</a>', '', ask, flags=re.S)
        tag = "button" if anchor else "div"
        attrs = f' type="button" data-pd-section="{C.esc(anchor[1])}"' if anchor else ""
        tasks.append(f'<{tag}{attrs} class="pd-task"><span class="pd-task-mark" aria-hidden="true"></span>'
                     f'<span>{text}</span><b aria-hidden="true">↗</b></{tag}>')
    outflow = a.outflow or {}
    dates = a.dates or {}
    source = orgs_snapshot(dates)
    potential = '+' + C.fmt_num(roll["fl_need"]) if source["available"] else "—"
    potential_count = (C.fmt_num(roll["k"]) + ' организаций'
                       if source["available"] else 'Данные недоступны')
    source_class = " is-stale" if source["stale"] or not source["available"] else ""
    health = (health.replace('<span class="hx-eyebrow">Здоровье портфеля</span>',
                             '<h2>Здоровье портфеля</h2>')
              .replace('<span class="hx-eyebrow">Цепочка решения</span>',
                       '<h3>Сценарий достижения плана</h3>'))
    health_panel = ('<section class="pd-health" '
                    'aria-label="Здоровье портфеля и сценарий достижения плана">'
                    + health + f'<p class="source-note{source_class}">'
                    + C.esc(source["note"]) + '</p></section>') if health else ""
    return (
        f'<div class="pd-overview" data-pd-index="{idx}" '
        f'data-closed="{C.esc(dates.get("closed_label", ""))}">'
        '<div class="pd-overview-head"><div>'
        f'<p class="pd-context">Прогноз на {C.esc(dates.get("label", a.ref_date))}</p>'
        f'<h2>{C.esc(a.tb_full)}</h2></div><span class="pd-status '
        f'{_tone(None if execution is None else execution - 1)}"><i></i>{state}</span></div>'
        + health_panel + '<div class="pd-metrics">'
        + _metric("Получатели зарплаты", a.verdict["rcp"], "человек")
        + _metric("Фонд оплаты труда", a.verdict["fot"], "млн ₽", 1e6)
        + '<article class="pd-metric pd-reserve"><div class="pd-metric-top">'
          '<h3>Потенциал роста</h3><span class="pd-reserve-icon" aria-hidden="true">↗</span></div>'
        f'<div class="pd-number">{potential}<small>человек</small></div>'
        f'<p class="pd-source-caption">Данные организаций: {C.esc(source["label"])}</p>'
        f'<div class="pd-metric-foot"><span>{potential_count}</span>'
        + _link(idx, "orgs", "Открыть", titles) + '</div></article></div>' + attention
        + '<div class="pd-analysis"><article class="pd-panel pd-trend">'
          '<div class="pd-panel-title"><h3>Динамика портфеля</h3>'
        + _link(idx, "trend", "Подробнее", titles) + '</div>'
        f'<div class="pd-trend-summary"><b class="{_tone(yoy)}">{yoy_text}</b>'
        f'<span>год к году</span><span class="pd-chart-period">{C.esc(period)}</span></div>'
        + _chart(rows) + '</article><article class="pd-panel pd-ranking">'
        f'<div class="pd-panel-title"><h3>Выполнение по {C.esc(a.unit_label)}</h3>'
        + _link(idx, "gosb", "Все", titles) + '</div>'
          '<p class="pd-ranking-note">Прогноз · черта на полосе — 100% плана</p>'
        f'<div class="pd-bank-list">{"".join(ranking) or "Нет данных по подразделениям"}</div>'
        '</article></div><div class="pd-secondary"><article class="pd-loss">'
        '<h3>Потери без возврата</h3>'
        f'<div class="pd-loss-number">{C.fmt_num(outflow.get("tot_kept") or 0)}'
        '<span>человек за 3 месяца</span></div>'
        f'<p>Вернули {C.fmt_num(outflow.get("ret_returned") or 0)} человек</p>'
        + _link(idx, "outflow", "Разобрать отток", titles)
        + '</article><article class="pd-actions"><div class="pd-panel-title">'
          f'<h3>В работе руководителя</h3><span>{len(tasks)} действий</span></div>'
        + ("".join(tasks) or '<p class="pd-empty">Дополнительных действий по текущим данным нет.</p>')
        + '</article></div><div class="pd-tools">'
          '<button type="button" class="pd-link" data-pd-management>'
          'Управление портфелем<span aria-hidden="true">↗</span></button>'
          '<button type="button" class="pd-link" data-pd-network>'
          'Портфель по всем ТБ<span aria-hidden="true">↗</span></button>'
          '</div></div>')
