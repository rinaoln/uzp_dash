"""Запросы слайдов по сегменту РГС.

УРОВЕНЬ ВИТРИНЫ. `uzp_dwh_company_holding_metric` на проме содержит РОВНО ОДИН
уровень — `level_name='gosb'`, `org_type='inn'` (профиль витрины: n_distinct=1 у
обеих колонок, выборка 1 млн строк, доля 1.0). Строк уровня `sb` и `tb` там нет,
и любой запрос с таким фильтром вернёт пусто. Поэтому всё читается с грейна ГОСБ
и сворачивается до организации, а территориальный банк берётся из справочника
подразделений — так же, как это делает отчёт tb_health.

Уровень и тип организации вынесены в параметры :lvl и :org: при расхождении с
промом их видно в логе сборки и в сообщении об ошибке, а не только в коде.
"""
from __future__ import annotations

# Единственный уровень витрины — см. преамбулу.
LEVEL = "gosb"
ORG_TYPE = "inn"

# ГОСБ → территориальный банк. Берём как есть, вместе с центральным аппаратом:
# слайд обязан сходиться по сумме, а выкинутый ЦА увёл бы часть портфеля из
# разреза по банкам, оставив её в итоге по сети.
_GMAP = """
  SELECT old_gosb_id,
         min(tb_id)         AS tb_id,
         min(tb_short_name) AS tb_short
  FROM {schema}.uzp_dim_gosb
  GROUP BY old_gosb_id
"""

# Какие месяцы вообще есть в витрине. Три последних и берём под разбор.
MONTHS = """
SELECT DISTINCT report_dt
FROM {schema}.uzp_dwh_company_holding_metric
WHERE level_name = :lvl AND org_type = :org
ORDER BY report_dt DESC
LIMIT 12
"""

# Диагностика: что в витрине ЕСТЬ, если запрос вернул пусто. Полный проход по
# двум enum-колонкам — дорого, поэтому запускается только по ошибке, зато
# сообщение называет причину вместо «данных нет».
LEVELS = """
SELECT level_name, org_type, count(*) AS n, max(report_dt) AS last_dt
FROM {schema}.uzp_dwh_company_holding_metric
GROUP BY level_name, org_type
"""

# Портфель организаций сегмента по трём месяцам, на грейне (ТБ, организация).
#
# LEFT JOIN со справочником подразделений намеренно: организация, чей ГОСБ в
# справочнике не нашёлся, не должна исчезать из итога по сегменту — она уйдёт в
# строку «—», и это будет видно.
#
# Даты перечислены поимённо, а не списком-параметром: IN :tuple требует
# expanding-параметра и ведёт себя по-разному в разных драйверах, а трёх
# именованных параметров достаточно и читается он однозначно.
ORGS = """
WITH gmap AS (""" + _GMAP + """)
SELECT COALESCE(g.tb_short, '—')         AS tb_short,
       m.org_id                          AS inn,
       m.report_dt                       AS report_dt,
       sum(COALESCE(m.current_fl_qty, 0)) AS fl
FROM {schema}.uzp_dwh_company_holding_metric m
JOIN {schema}.uzp_dim_company dc ON dc.inn = m.org_id
LEFT JOIN gmap g ON g.old_gosb_id = m.level_id
WHERE m.level_name = :lvl
  AND m.org_type = :org
  AND dc.segment_name IN :segs
  AND m.report_dt IN (CAST(:m0 AS date), CAST(:m1 AS date), CAST(:m2 AS date))
GROUP BY COALESCE(g.tb_short, '—'), m.org_id, m.report_dt
"""

# Справка по организации: имя и холдинг. Отдельным запросом, чтобы не тащить
# текстовые поля через группировку по месяцам.
COMPANY = """
SELECT dc.inn               AS inn,
       min(dc.company_name) AS company_name,
       min(dc.holding_name) AS holding_name
FROM {schema}.uzp_dim_company dc
WHERE dc.segment_name IN :segs
GROUP BY dc.inn
"""

# Какие значения сегмента есть в справочнике — для сообщения об ошибке, когда
# ни одно из ожидаемых не нашлось (на проме имя сегмента может отличаться).
SEGMENTS = """
SELECT segment_name, count(*) AS n
FROM {schema}.uzp_dim_company
GROUP BY segment_name
ORDER BY count(*) DESC
"""


# ОТРАСЛЬ ОРГАНИЗАЦИИ — из витрины атрибутов ЕПК.
#
# Поля «ведомство» в витринах нет, а отрасль есть: 21 категория, среди них
# «Органы гос. и мун. управления» и «Здравоохранение, образование». Это грубее
# ведомства (разрез по вывеске различает школу, больницу и суд, отрасль — нет),
# но это ЗАПИСАННЫЙ признак, а не вывод по наименованию, и показать его стоит.
#
# ЗАПОЛНЕНО ОНО ДАЛЕКО НЕ ВЕЗДЕ: по профилю прома — у 9,7% организаций. Поэтому
# отрасль нигде не заменяет ведомство: она идёт отдельным разрезом со строкой
# «Отрасль не указана», а в разборе ведомства работает только подсказкой там,
# где наименование ничего не говорит (см. agency.INDUSTRY_HINTS).
#
# Два запроса, а не один с OR в условии соединения: соединение по двум ключам
# через OR планировщик разворачивает в перебор, и на витрине ЕПК это очень
# дорого. По epk_id связь точная (это ключ витрины), по ИНН — запасная, ею
# добираются организации, чей epk_id в витрине не нашёлся.
INDUSTRY_BY_EPK = """
SELECT dc.inn                  AS inn,
       min(e.industry_name)    AS industry_name
FROM {schema}.uzp_dim_company dc
JOIN {schema}.uzp_data_epk_consolidation e ON e.epk_id = dc.epk_id
WHERE dc.segment_name IN :segs
  AND e.industry_name IS NOT NULL
GROUP BY dc.inn
"""

INDUSTRY_BY_INN = """
SELECT e.inn                   AS inn,
       min(e.industry_name)    AS industry_name
FROM {schema}.uzp_data_epk_consolidation e
JOIN {schema}.uzp_dim_company dc ON dc.inn = e.inn
WHERE dc.segment_name IN :segs
  AND e.industry_name IS NOT NULL
  AND e.inn IS NOT NULL
GROUP BY e.inn
"""
