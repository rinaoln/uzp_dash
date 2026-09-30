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

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from ...registry import Context, dashboard
from ...render import components as C
from ...render import page
from ... import progress
from ..tb_health import analyze as th
from ..tb_health import bank, view as base
from . import analyze

_ASSETS = Path(__file__).parent / "assets"

TITLE = "Работа с оттоком получателей заработной платы"
SUBTITLE = "Кого потеряли, вернули ли и что с этим делали"


@dashboard("outflow")
def build(ctx: Context) -> str:
    """Отчёт по оттоку: банк целиком и по вкладке на каждый ТБ."""
    b = bank.load(ctx)
    hist = analyze.load_history(ctx, b)
    clients = analyze.load_clients(ctx, b)
    # Разбор комментариев по клиентам: один пул на весь отчёт и один запрос
    # текстов. Уровни его не пересчитывают — они берут готовые выводы: тексты
    # одни и те же, а двенадцать проходов по самой большой таблице стоили бы
    # минут и денег на вызовах модели.
    ins_gosb, ins_tb = {}, {}
    if th.audit_enabled(ctx):
        pool = analyze.audit_pool(b)
        texts = bank.audit_texts(ctx.engine, b,
                                 sorted({int(x) for x in pool["inn"]}) if not pool.empty
                                 else [])
        ins_gosb, _stats = analyze.audit(ctx, b, pool, texts)
        ins_tb = analyze.audit_by_tb(ins_gosb, b.tb_of, pool)
    else:
        progress.done("Разбор комментариев отключён (llm_max_calls=0): у клиентов "
                      "останутся причина ухода и обещание из чек-листа")

    levels_tb = sorted(((int(r.tb_id), str(r.tb_short_name), str(r.tb_full_name))
                        for r in b.tbs.itertuples()), key=lambda x: th._ru_key(x[1]))
    progress.step("Уровень СБ: отток по всему банку")
    sb = analyze.prepare(b, hist, clients, None, "СБ",
                         "Сбербанк — все территориальные банки", ins_tb)
    levels = [sb]
    for i, (tb_id, short, full) in enumerate(levels_tb, start=1):
        progress.step(f"═══ ТБ {short} ({i} из {len(levels_tb)}) ═══")
        levels.append(analyze.prepare(b, hist, clients, tb_id, short, full,
                                      ins_gosb))
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
        # своя тема идёт ПОСЛЕ файлов дизайнера и переопределяет только токены
        # и те компоненты, по которым отчёты должны различаться
        css=(base._asset("ux.css") + base._asset("ux-fix.css")
             + base._asset("help.css") + _asset("theme.css")),
        tail=(f'<script>{base._asset("ux.js.txt")}</script>\n'
              f'{base._HUB_JS}\n{base._REVEAL_JS}\n{base._XLS_JS}\n{_CELLS_JS}\n'
              # памятка: своя разметка, свой скрипт (свой ключ «не показывать»),
              # а шаги листает общий скрипт первого отчёта — он про разметку, а
              # не про содержание, и дублировать его нечем
              # окно разбора подразделения — прямо в <body>, вне уровней:
              # <dialog> внутри скрытого поддерева открывается нулевого размера
              f'{_UNIT_DLG}\n'
              f'{_asset("help.html")}<script>\n{_asset("help.js")}</script>\n'
              f'{base._HELP_STEPS_JS}\n'
              f'<div id="print-root" aria-hidden="true"></div>'),
    )


def _asset(name: str) -> str:
    """Файл оформления этого отчёта (свои стили лежат рядом с его кодом)."""
    with open(_ASSETS / name, encoding="utf-8", newline="") as f:
        return f.read()


# Раскрытие ячейки матрицы. Таблица собирается СКРИПТОМ из данных уровня: готовая
# разметка на все ячейки всех уровней весит в разы больше самих чисел, а
# открывают за сеанс одну-две.
_UNIT_DLG = ('<dialog class="gd of-dlg" id="of-unit" aria-label="Разбор '
             'подразделения"><div class="gd-sheet" id="of-unit-body"></div></dialog>')

_CELLS_JS = """
<script>
(function(){
  function esc(s){
    return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
    });
  }
  function num(n){ return Number(n || 0).toLocaleString('ru-RU'); }
  function said(r){
    /* что сказано в задаче по этому клиенту: причина ухода и обещание вернуть.
       Пусто — значит в тексте задачи об этом ничего нет; утверждать по этому,
       что работы не было, отчёт не вправе */
    var bits = [];
    if(r.reason) bits.push(esc(r.reason)
      + (r.rsrc ? ' <i style="color:var(--text-2)">(' + esc(r.rsrc) + ')</i>' : ''));
    if(r.promise) bits.push(esc(r.promise));
    /* вывод разбора комментариев: что мешает и что делать. Отбит цветом, чтобы
       не путать с тем, что сотрудник записал в задачу своими руками */
    if(r.aud) bits.push('<span class="of-aud">' + esc(r.aud) + '</span>');
    if(r.aud_do) bits.push('<span class="of-aud-do">→ ' + esc(r.aud_do) + '</span>');
    return bits.length ? bits.join('<br>') : '\u2014';
  }
  function clear(card){
    var on = card.querySelectorAll('td.of-cell.on');
    for(var i = 0; i < on.length; i++) on[i].classList.remove('on');
  }
  function scope(el){
    /* у каждого разреза своя вкладка и своя плашка клиентов: искать по .card
       нельзя — нашлась бы плашка первого разреза, а открыт другой */
    return el.closest('.of-pane') || el.closest('.card');
  }
  window.ofDrillClose = function(el){
    var box = el.classList && el.classList.contains('of-drill')
      ? el : (scope(el) || document).querySelector('.of-drill');
    if(box){ box.hidden = true; box.dataset.key = ''; }
    var sc = scope(el);
    if(sc){
      clear(sc);
      var on = sc.querySelectorAll('.gcard.on');
      for(var i = 0; i < on.length; i++) on[i].classList.remove('on');
    }
  };
  window.ofUnitClose = function(){
    var d = document.getElementById('of-unit');
    if(d && d.open){ if(d.close) d.close(); else d.removeAttribute('open'); }
  };
  window.ofUnit = function(btn){
    var dlg = document.getElementById('of-unit');
    var box = document.getElementById('of-unit-body');
    if(!dlg || !box) return;
    var lvl = btn.closest('.lvl');
    var key = btn.getAttribute('data-unit') || '';
    var u = ((window.__OFUNITS || {})[lvl ? lvl.id : ''] || {})[key];
    if(!u) return;
    var segs = (u.segs || []).map(function(sg){
      return '<tr><td>' + esc(sg.seg) + '</td><td class="num"><b>'
           + num(sg.kept) + '</b></td><td class="num">'
           + (u.kept ? Math.round(sg.kept / u.kept * 100) : 0) + '%</td></tr>';
    }).join('');
    /* клиентов собираем из перечней ячеек этого же разреза: список уже лежит в
       файле, и второй такой же ради карточки клали бы зря */
    var pref = key.split('|')[0] + '|' + key.split('|')[1] + '|';
    var all = (window.__OFCELLS || {})[lvl ? lvl.id : ''] || {};
    var agg = {};
    Object.keys(all).forEach(function(k){
      if(k.indexOf(pref) !== 0) return;
      (all[k].rows || []).forEach(function(r){
        var c = agg[r.name] || { name: r.name, gone: 0, ret: 0, kept: 0 };
        c.gone += r.gone; c.ret += r.ret; c.kept += r.kept;
        if(!c.reason && r.reason){ c.reason = r.reason; c.rsrc = r.rsrc; }
        if(!c.promise && r.promise) c.promise = r.promise;
        if(!c.aud && r.aud){ c.aud = r.aud; c.aud_do = r.aud_do; }
        agg[r.name] = c;
      });
    });
    var rows = Object.keys(agg).map(function(k){ return agg[k]; })
      .sort(function(a, b){ return b.kept - a.kept; }).slice(0, 10)
      .map(function(r){
        return '<tr><td>' + esc(r.name) + '</td><td class="num">' + num(r.gone)
             + '</td><td class="num">' + (r.ret ? num(r.ret) : '\u2014')
             + '</td><td class="num"><b>' + num(r.kept) + '</b></td>'
             + '<td class="of-said">' + said(r) + '</td></tr>';
      }).join('');
    var dyn = [];
    if(u.delta !== null && u.delta !== undefined){
      dyn.push('к ' + esc(u.prev) + ' ' + (u.delta > 0 ? '+' : '\u2212')
               + num(Math.abs(u.delta)) + ' чел (было ' + num(u.was) + ')');
    }
    if(u.yoy !== null && u.yoy !== undefined){
      dyn.push('к ' + esc(u.yoy_label) + ' ' + (u.yoy > 0 ? '+' : '\u2212')
               + num(Math.abs(u.yoy)) + ' чел (было ' + num(u.yoy_was) + ')');
    }
    box.innerHTML =
      '<div class="gd-head"><div><h3 style="margin:0">' + esc(u.name) + '</h3>'
      + '<div class="gd-note">' + esc(u.period) + '</div>'
      + '<div class="gd-fc">Потеряли безвозвратно <b>' + num(u.kept)
      + '</b> чел \u00b7 ушло ' + num(u.gone) + ' \u00b7 вернулось ' + num(u.ret)
      + ' \u00b7 ушли из ' + num(u.orgs) + ' организаций</div>'
      + (dyn.length ? '<div class="gd-note">' + dyn.join(' \u00b7 ') + '</div>' : '')
      + '</div><div class="gd-head-actions">'
      + '<button type="button" class="gd-close" onclick="ofUnitClose()" '
      + 'aria-label="Закрыть">\u00d7</button></div></div>'
      + (segs ? '<div class="gd-block"><h4>По каким сегментам ушли</h4>'
        + '<table><thead><tr><th>Сегмент</th><th class="num">потери, чел</th>'
        + '<th class="num">доля</th></tr></thead><tbody>' + segs
        + '</tbody></table></div>' : '')
      + (rows ? '<div class="gd-block"><h4>Кого потеряли крупнее всего</h4>'
        + '<table><thead><tr><th>Клиент</th><th class="num">отток, чел</th>'
        + '<th class="num">возврат, чел</th><th class="num">потери, чел</th>'
        + '<th>что сказано в задаче</th></tr>'
        + '</thead><tbody>' + rows + '</tbody></table>'
        + '<p class="of-drill-rest">Здесь десять крупнейших. Полный список по '
        + 'паре подразделение\u2013сегмент открывается в матрице потерь.</p>'
        + '</div>' : '');
    if(typeof dlg.showModal === 'function'){ if(!dlg.open) dlg.showModal(); }
    else { dlg.setAttribute('open', ''); }
  };
  document.addEventListener('click', function(e){
    /* клик по подложке закрывает окно: <dialog> сам этого не делает */
    if(e.target && e.target.id === 'of-unit') window.ofUnitClose();
  });
  window.ofCellKey = function(e, td){
    if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); window.ofCell(td); }
  };
  window.ofCell = function(td){
    var card = scope(td); if(!card) return;
    var box = card.querySelector('.of-drill'); if(!box) return;
    var lvl = td.closest('.lvl');
    var key = td.getAttribute('data-cell') || '';
    var data = ((window.__OFCELLS || {})[lvl ? lvl.id : ''] || {})[key];
    if(!data) return;
    /* повторный клик по той же ячейке закрывает плашку: иначе единственный
       способ её убрать — крестик, и это неочевидно */
    if(box.dataset.key === key && !box.hidden){ window.ofDrillClose(box); return; }
    clear(card);
    td.classList.add('on');
    var tr = td.closest('tr');
    var unit = (tr && tr.cells.length) ? tr.cells[0].textContent.trim() : '';
    var seg = data.seg || '';
    var rows = (data.rows || []).map(function(r){
      return '<tr><td>' + esc(r.name) + '</td>'
           + '<td class="num">' + num(r.gone) + '</td>'
           + '<td class="num">' + (r.ret ? num(r.ret) : '\u2014') + '</td>'
           + '<td class="num"><b>' + num(r.kept) + '</b></td>'
           + '<td class="of-said">' + said(r) + '</td></tr>';
    }).join('');
    var share = data.ret && data.gone ? Math.round(data.ret / data.gone * 100) : 0;
    var rest = data.rest_n
      ? '<p class="of-drill-rest">Ещё ' + num(data.rest_n) + ' '
        + (data.rest_n % 10 === 1 && data.rest_n % 100 !== 11 ? 'клиент' : 'клиентов')
        + ' этой пары дают ' + num(data.rest_kept) + ' чел потерь.</p>'
      : '';
    var per = key.split('|')[0];
    var pname = per === 'win' ? 'три месяца' : per;
    box.innerHTML =
      '<div class="of-drill-head"><div class="of-drill-t">' + esc(unit) + ' \u00b7 '
      + esc(seg) + ' \u00b7 <span class="of-drill-p">' + esc(pname) + '</span></div>'
      + '<button type="button" class="of-drill-x" onclick="ofDrillClose(this)">'
      + 'Закрыть</button></div>'
      + '<div class="of-drill-sub">Потеряли безвозвратно ' + num(data.kept)
      + ' чел \u00b7 ушло ' + num(data.gone) + ' \u00b7 вернулось ' + num(data.ret)
      + ' (' + share + '%) \u00b7 организаций: ' + num(data.n) + '</div>'
      + '<table><thead><tr><th>Клиент</th><th class="num">отток, чел</th>'
      + '<th class="num">возврат, чел</th><th class="num">потери, чел</th>'
      + '<th>что сказано в задаче</th></tr>'
      + '</thead><tbody>' + rows + '</tbody></table>' + rest;
    box.dataset.key = key;
    box.hidden = false;
  };
})();
</script>
"""


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
            # перечни клиентов и разбор карточек — данными на весь уровень:
            # их читают и матрица, и детализация
            + _cells_data(lvl, idx) + base._contacts())


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
        dyn = "сравнить не с чем: данных за предыдущий период нет"
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
        f'<div class="row2">{dyn}</div>{_hero_months(t)}'
        + _how(
            '<b>Окно отчёта</b> — три последних закрытых месяца. Месяц считается '
            'закрытым, когда ведомости по нему собраны полностью; текущий месяц в '
            'отчёт не попадает, иначе его пришлось бы сравнивать с полными.',
            '<b>Ушло</b> — люди, которые в этом окне перестали получать у нас '
            'зарплату. <b>Вернулось</b> — из них те, кто снова её получает. '
            'Разница между ними и есть <b>безвозвратные потери</b>: именно по ним '
            'считается всё остальное в отчёте.',
            '<b>Растут или снижаются</b> — сравнение с предыдущими тремя месяцами. '
            'Плана по оттоку нет ни в одной витрине, поэтому отчёт не говорит '
            '«много» или «мало»: он говорит, стало хуже или лучше, чем было.',
            title='Как считаются эти цифры'))
    return C.card(inner, cls=f"hero {st}")


def _hero_months(t: dict) -> str:
    """Строка вердикта с двумя сравнениями последнего закрытого месяца.

    Окно в три месяца отвечает на вопрос «куда идём», но руководителю нужен и
    последний месяц: что изменилось только что и что изменилось за год. Обе
    цифры стоят рядом, потому что порознь они спорят друг с другом — рост к
    июлю при падении к прошлому августу это сезон, а не ухудшение.
    """
    bits = [f'{name} {_delta_b(c["delta"])}'
            for k, name in (("mom", "месяц к месяцу"), ("yoy", "год к году"))
            for c in [t.get(k)] if c]
    if not bits:
        return ""
    cur = (t.get("mom") or t.get("yoy"))
    return (f'<div class="row2">Последний закрытый месяц '
            f'{C.month_ru(cur["cur_label"])} — потери '
            f'<b>{C.fmt_num(cur["kept"])}</b> чел: {" · ".join(bits)}</div>')


def _delta_b(delta: float) -> str:
    """Изменение потерь: рост — красным, снижение — зелёным, ноль — обычным."""
    if not delta:
        return '<b>без изменений</b>'
    col = "var(--bad)" if delta > 0 else "var(--good)"
    sign = "+" if delta > 0 else "−"
    return f'<b style="color:{col}">{sign}{C.fmt_num(abs(delta))} чел</b>'


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
        t = lvl.totals or {}
        # на плашке две строки: сравнения месяца важнее счётчика месяцев в
        # графике — его и так видно в заголовке раздела
        nums = []
        for k, name in (("mom", "чел · месяц к месяцу"), ("yoy", "чел · год к году")):
            c = t.get(k)
            if not c:
                continue
            sign = "+" if c["delta"] > 0 else ("−" if c["delta"] < 0 else "±")
            nums.append((f'{sign}{C.fmt_num(abs(c["delta"]))}', name,
                         "bad" if c["delta"] > 0 else ""))
        if not nums:
            nums.append((f'{len(lvl.months)}', "закрытых месяцев в графике", ""))
        if len(nums) < 2:
            nums.append((C.fmt_num(worst["kept"]),
                         f'худший месяц — {worst["label"]}', "bad"))
        out["trend"] = ("Как потери и возвраты шли месяц за месяцем.", nums[:2])
    if not lvl.matrix.empty:
        n_units = lvl.matrix["unit_id"].nunique()
        out["matrix"] = (f"Где теряем: {unit} и сегмент, за три месяца и помесячно.",
                         [(C.fmt_num(lvl.matrix["kept"].sum()), "чел потерь в матрице",
                           "bad"),
                          (f'{n_units} × {len(lvl.segs)}', f'{unit} и сегментов', "")])
    if lvl.units:
        worst = lvl.units[0]
        out["units"] = (f"Потери каждого {unit}, возврат и куда всё это движется.",
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
        out["orgs"] = ("Кого потеряли — списком, с выгрузкой в Excel.",
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
    how = _how(
        '<b>Месяц</b> — тот, в котором человек перестал получать зарплату, а не '
        'тот, в котором мы это увидели. Поэтому цифры прошлых месяцев не меняются '
        'задним числом, даже если возврат случился позже.',
        '<b>Потери месяца</b> = ушло минус вернулось. Возврат засчитывается '
        'месяцу ухода: иначе один и тот же человек попал бы в два месяца сразу.',
        '<b>Сравнения.</b> Месяц к месяцу — с предыдущим календарным месяцем. Год '
        'к году — с тем же месяцем прошлого года: август сравнивается с августом, '
        'а не с июлем, поэтому сезонность не выдаёт себя за рост.',
        '<b>Если сравнивать не с чем</b> — строки просто не будет. Подставлять '
        'соседний месяц под чужой подписью отчёт не станет.')
    return C.section("Динамика оттока за 12 месяцев",
                     lead + _month_cmp(lvl) + C.card(how + _bars(m) + tbl),
                     eyebrow="Ретроспектива потерь",
                     desc="Сколько людей уходило и сколько возвращалось месяц за "
                          "месяцем — чтобы отличить разовый уход крупного клиента "
                          "от устойчивого роста потерь. Ниже — разрезы окна: "
                          "три месяца накопительно и каждый месяц с двумя "
                          "динамиками — к предыдущему месяцу и к тому же месяцу "
                          "год назад.")


def _how(*lines: str, title: str = "Как считаются эти цифры") -> str:
    """Значок «i» рядом с заголовком: откуда взялось число.

    Раскрывается кликом и закрыт по умолчанию — объяснение нужно один раз, а
    место на экране нужно всегда. Внутри — про смысл числа, а не про таблицы и
    поля витрины: руководителю они ничего не говорят.
    """
    return base._gd_help(*lines, title=title)


def _month_cmp(lvl: analyze.Level) -> str:
    """Разрезы окна: накопительно и каждый месяц — с обеими динамиками.

    Четыре карточки рядом, без переключателя: сравнивают их друг с другом, а не
    читают по очереди. В месячной карточке главное число — потери месяца, под
    ним два сравнения: к предыдущему месяцу и к тому же месяцу год назад. Первое
    отвечает «что изменилось только что», второе снимает сезонность.
    """
    if not lvl.periods:
        return ""
    cards = "".join(_cmp_card(p) for p in lvl.periods)
    return f'<div class="grid cols-2">{cards}</div>' if cards else ""


def _cmp_card(p: dict) -> str:
    """Карточка одного разреза: потери периода и его динамика."""
    t = p.get("totals") or {}
    if not t:
        return ""
    if p.get("is_month"):
        lines = [_cmp_line(t.get("mom"), "к предыдущему месяцу"),
                 _cmp_line(t.get("yoy"), "к тому же месяцу год назад")]
        lines = [x for x in lines if x]
        body = (f'<div class="delta">{lines[0]}</div>' if lines else "")
        foot = ("".join(f'<div>{x}</div>' for x in lines[1:])
                + f'<div>ушло {C.fmt_num(t["gone"])} чел · вернулось '
                  f'{C.fmt_num(t["ret"])} ({(t.get("ret_share") or 0) * 100:.0f}%)</div>')
    else:
        d = t.get("delta_kept")
        prev = ", ".join(_m(x) for x in (t.get("prev_months") or []))
        body = (f'<div class="delta">к предыдущим трём месяцам '
                f'({C.esc(prev)}) {_delta_b(d)}</div>' if d is not None else "")
        foot = (f'<div>ушло {C.fmt_num(t["gone"])} чел · вернулось '
                f'{C.fmt_num(t["ret"])} ({(t.get("ret_share") or 0) * 100:.0f}%)</div>')
    return C.card(
        f'<div class="label">{C.esc(p["label"])}</div>'
        f'<div class="value" style="color:var(--bad)">{C.fmt_num(t["kept"])} чел</div>'
        f'{body}<div class="foot">{foot}</div>', cls="kpi")


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


def _cmp_line(c: dict | None, name: str) -> str:
    """Строка сравнения: «к предыдущему месяцу +20 430 чел (+218%) — май 2026»."""
    if not c:
        return ""
    pct = c.get("pct")
    sign = "+" if c["delta"] > 0 else "−"
    pct_s = (f' ({sign}{abs(pct):.0f}%)' if pct is not None and c["delta"] else "")
    return (f'{name} {_delta_b(c["delta"])}{pct_s} — было '
            f'{C.fmt_num(c["base_kept"])} в {C.esc(c["base_label"])}')


# --------------------------------------------------------------------------- #
def _matrix_section(lvl: analyze.Level, idx: int) -> str:
    """Матрица «единица × сегмент» по безвозвратным потерям — в четырёх разрезах.

    Переключатель вверху: три месяца накопительно и каждый месяц отдельно. Одна
    матрица на окно отвечала только «где теряем вообще» — по месяцам видно, где
    потери пришли разом, а где идут ровным фоном, и это разные разговоры.

    Цвет — не статус выполнения плана (его у оттока нет), а вес ячейки: чем
    темнее, тем большую часть потерь СВОЕГО разреза даёт эта пара.

    Ячейка раскрывается: под таблицей появляется плашка с перечнем клиентов,
    которые эти потери и составили, — за тот же период, что и сама матрица.
    """
    if not lvl.periods:
        return ""
    items = []
    for p in lvl.periods:
        html = _matrix_table(lvl, p)
        if not html:
            continue
        t = p.get("totals") or {}
        items.append((p["short"], f'потери {C.fmt_num(t.get("kept", 0))} чел', html))
    if not items:
        return ""
    hint = ('<p class="sub" style="font-size:14px;margin:-4px 0 12px">'
            'В ячейке — сколько человек потеряли безвозвратно за выбранный период. '
            'Чем темнее, тем большая часть потерь приходится на эту пару. '
            '<b>Нажмите на ячейку</b> — под таблицей появятся клиенты, из '
            'которых она сложилась.</p>')
    how = _how(
        f'<b>В ячейке</b> — сколько человек {lvl.unit_label} потерял безвозвратно '
        'в этом сегменте за выбранный период. Три месяца в сумме дают ровно '
        'столько же, сколько накопительный разрез: это одни и те же строки.',
        '<b>Заливка</b> считается от самой тяжёлой ячейки разреза, а не от общей '
        'шкалы отчёта. Поэтому тёмное в апреле и тёмное в июне — разные величины, '
        'сравнивать нужно числа, а не цвет.',
        '<b>Сегмент</b> берётся из витрины оттока — из той же строки, что и сам '
        'отток. Справочник клиентов тут не участвует: там сегмент мог смениться '
        'после ухода, и потери разъехались бы по колонкам.',
        '<b>Перечень клиентов</b> в раскрытой ячейке — за тот же период, что и '
        'сама ячейка. Показаны двенадцать крупнейших, остальные свёрнуты в строку '
        'под таблицей, но в сумму ячейки входят все.',
        '<b>Колонка «что сказано в задаче»</b> собрана из текста задач: причина '
        'ухода и обещание вернуть людей — так, как их записал сотрудник, с '
        'пометкой, из чек-листа это или из комментария. Зелёным ниже — вывод '
        'разбора комментариев: что мешает вернуть и что делать. Прочерк означает '
        'только одно: в тексте задач об этом ничего нет.')
    return C.section(f"Матрица потерь {lvl.unit_label}/сегмент",
                     C.card('<h3>Безвозвратные потери, чел</h3>' + hint + how
                            + _period_switch(items)),
                     eyebrow="Где именно теряем",
                     desc="Где сосредоточен отток: подразделение и сегмент, за "
                          "три месяца и за каждый месяц. Клик по ячейке "
                          "показывает клиентов, из которых она сложилась.")


def _matrix_table(lvl: analyze.Level, p: dict) -> str:
    """Таблица матрицы одного разреза плюс место под перечень клиентов."""
    m = p.get("matrix")
    segs = p.get("segs") or []
    if m is None or m.empty or not segs:
        return ""
    units = (m.groupby(["unit_id", "unit_name"])["kept"].sum()
              .reset_index().sort_values("kept", ascending=False))
    cells = {(int(r.unit_id), r.seg_name): float(r.kept) for r in m.itertuples()}
    top = max(cells.values(), default=0) or 1
    has = lvl.cells or {}
    head = ("<th>" + C.esc(lvl.unit_label) + "</th>"
            + "".join(f'<th class="num">{C.esc(s)}</th>' for s in segs)
            + '<th class="num">всего</th>')
    rows = []
    for r in units.itertuples():
        uid, name = int(r.unit_id), str(r.unit_name)
        tds = [f'<td>{C.esc(name[:26])}</td>']
        for sg in segs:
            v = cells.get((uid, sg))
            if not v:
                tds.append('<td class="num" style="color:var(--text-2)">—</td>')
                continue
            share = v / top
            bg = f"rgba(176,56,46,{0.08 + share * 0.55:.3f})"
            key = f'{p["key"]}|{uid}|{sg}'
            # кликабельна только та ячейка, для которой перечень действительно
            # собран: «нажимается, но ничего не открывает» читается как поломка
            can_open = key in has
            attrs = (f' data-cell="{C.esc(key)}" tabindex="0" role="button" '
                     f'onclick="ofCell(this)" onkeydown="ofCellKey(event,this)"'
                     if can_open else "")
            cls = "num heat of-cell" if can_open else "num heat"
            tip = (f'{C.esc(name)} · {C.esc(sg)}: потеряли {C.fmt_num(v)} чел'
                   + (" · нажмите, чтобы увидеть клиентов" if can_open else ""))
            tds.append(f'<td class="{cls}" style="background:{bg}" '
                       f'title="{tip}"{attrs}>{C.fmt_num(v)}</td>')
        tds.append(f'<td class="num"><b>{C.fmt_num(r.kept)}</b></td>')
        rows.append(f'<tr>{"".join(tds)}</tr>')
    return (f'<div style="overflow-x:auto"><table class="matrix"><thead><tr>{head}'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<div class="of-drill" hidden aria-live="polite"></div>')


def _cells_data(lvl: analyze.Level, idx: int) -> str:
    """Перечни клиентов ячеек — данными, а не готовой разметкой.

    Ключ ячейки — «разрез|единица|сегмент»: разрезов четыре, и перечень июня не
    должен открыться под матрицей апреля. Разметка на все ячейки всех разрезов
    весит в разы больше самих чисел, а открывают за сеанс одну-две, поэтому в
    файл кладутся данные, а таблицу собирает скрипт в момент клика.
    """
    if not lvl.cells:
        return ""
    data = json.dumps(lvl.cells, ensure_ascii=False).replace("<", "\\u003c")
    units = {k: v for p in (lvl.periods or [])
             for k, v in (p.get("unit_cards") or {}).items()}
    udata = json.dumps(units, ensure_ascii=False).replace("<", "\\u003c")
    return (f'<script>window.__OFCELLS=window.__OFCELLS||{{}};'
            f'window.__OFCELLS["lvl-{idx}"]={data};'
            f'window.__OFUNITS=window.__OFUNITS||{{}};'
            f'window.__OFUNITS["lvl-{idx}"]={udata};</script>')


def _period_switch(items: list) -> str:
    """Переключатель разрезов — та же механика, что у списков работы.

    Отдельного скрипта здесь нет намеренно: `ofTab` из первого отчёта умеет
    ровно это и уже проверен. Без JS страница покажет все разрезы подряд, а не
    потеряет три из четырёх.
    """
    if not items:
        return ""
    if len(items) == 1:
        return f'<div class="of-wrap"><div class="of-pane">{items[0][2]}</div></div>'
    tabs = "".join(
        f'<button type="button" class="of-tab{" on" if i == 0 else ""}" role="tab" '
        f'aria-selected="{"true" if i == 0 else "false"}" onclick="ofTab(this,{i})">'
        f'<span class="of-tab-t">{C.esc(t)}</span>'
        f'<span class="of-tab-n">{C.esc(sub)}</span></button>'
        for i, (t, sub, _h) in enumerate(items))
    panes = "".join(f'<div class="of-pane" role="tabpanel">{h}</div>'
                    for _t, _s, h in items)
    lead = ('<p class="sub" style="font-size:14px;margin:0 0 10px">'
            'Можно смотреть три месяца сразу или каждый месяц отдельно.</p>')
    return (f'<div class="of-wrap">{lead}'
            f'<div class="of-tabs" role="tablist">{tabs}</div>{panes}</div>')


def _units_section(lvl: analyze.Level, idx: int, lvl_of: dict | None = None) -> str:
    """Карточки единиц в четырёх разрезах: окно и каждый месяц отдельно.

    На уровне банка карточка ТБ ведёт в его собственный разбор — это и есть
    переход СБ → ТБ, тот же, что в первом отчёте. Переход стоит только в
    накопительном разрезе: он открывает уровень целиком, а не выбранный месяц,
    и ставить его под месячной карточкой значило бы обещать не то.
    """
    if not lvl.periods:
        return ""
    items = []
    for p in lvl.periods:
        html = _unit_cards(lvl, p, lvl_of if not p.get("is_month") else None)
        if not html:
            continue
        t = p.get("totals") or {}
        items.append((p["short"], f'потери {C.fmt_num(t.get("kept", 0))} чел', html))
    if not items:
        return ""
    how = _how(
        f'<b>Потери</b> — ушло минус вернулось по всем клиентам этого '
        f'{lvl.unit_label} за выбранный период.',
        '<b>Сколько организаций</b> — это разные клиенты: одна организация считается '
        'один раз, даже если люди уходили из неё два месяца подряд.',
        '<b>Стрелка динамики</b> в месячном разрезе смотрит на предыдущий месяц, '
        'в накопительном — на предыдущие три. Год к году показан там же, где '
        'месяц: он снимает сезонность.',
        '<b>Цвет карточки</b> — по направлению движения: потери выросли, держатся '
        'или снизились. Норматива «сколько терять можно» нет, и отчёт его не '
        'придумывает.')
    return C.section(f"Потери по {lvl.unit_label}",
                     C.card(how + _period_switch(items)),
                     eyebrow=f"Детализация по {lvl.unit_label}",
                     desc="Сколько потерял каждый ГОСБ или ТБ — за три месяца "
                          "и за каждый месяц. В месячном разрезе карточка "
                          "сравнивает месяц с предыдущим и с тем же месяцем год "
                          "назад, а раскрытая карточка показывает, из каких "
                          "сегментов и клиентов сложились потери.")


def _unit_cards(lvl: analyze.Level, p: dict, lvl_of: dict | None) -> str:
    """Карточки подразделений одного разреза.

    Карточка раскрывается: под сеткой появляется разбор — из каких сегментов
    сложились потери и какие клиенты в них крупнейшие. До этого карточка
    показывала итог и упиралась в тупик: дальше идти было некуда, а первый же
    вопрос к любому числу — «из чего оно».
    """
    units = p.get("units") or []
    if not units:
        return ""
    is_month = bool(p.get("is_month"))
    cards = []
    for u in units:
        delta = u.get("delta")
        st = _status(delta)
        if delta is None:
            dyn = '<div class="g-act">сравнить не с чем: прошлого периода нет</div>'
        else:
            base = (f'к {C.month_ru(u.get("prev_label", ""), "dat")}' if is_month
                    else "к прошлым трём месяцам")
            dyn = (f'<div class="g-act">{base} {_delta_b(delta)} '
                   f'(было {C.fmt_num(u["was_kept"])})</div>')
        if is_month and u.get("yoy_delta") is not None:
            dyn += (f'<div class="g-act">к {C.month_ru(u.get("yoy_label", ""), "dat")} '
                    f'{_delta_b(u["yoy_delta"])} '
                    f'(было {C.fmt_num(u["yoy_kept"])})</div>')
        ret_pct = (u.get("ret_share") or 0) * 100
        n = int(u.get("orgs") or 0)
        inner = (
            f'<div class="g-head"><h3 style="margin:0">{C.esc(u["name"])}</h3>'
            f'<span class="g-ex {st}"><i>потери</i>{C.fmt_num(u["kept"])}</span></div>'
            f'<div class="g-fc">ушло {C.fmt_num(u["gone"])} · вернулось '
            f'{C.fmt_num(u["ret"])} ({ret_pct:.0f}%)</div>'
            f'<div style="margin:2px 0 6px">'
            + C.badge(f'ушли из {C.fmt_num(n)} '
                      f'{C.plural(n, "организации", "организаций", "организаций")}',
                      "warn") + '</div>'
            + dyn)
        go = (lvl_of or {}).get(u["id"])
        more = (f'<button type="button" class="g-more u-open" '
                f'data-unit="{p["key"]}|{u["id"]}" onclick="ofUnit(this)">'
                f'Показать, из чего сложились потери</button>')
        drill = (f'<div class="g-more g-drill" onclick="event.stopPropagation();'
                 f'lvlGo({go})">Открыть разбор {C.esc(u["name"])} →</div>'
                 if go else "")
        cards.append(f'<div class="card gcard {st}">{inner}{more}{drill}</div>')
    return f'<div class="gcards">{"".join(cards)}</div>'


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
            (C.esc(r.get("group", ""))
             + (f'<div class="gd-emp">разбор: {C.esc(r["action"])}</div>'
                if r.get("action") else "")),
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
