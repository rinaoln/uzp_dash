"""Запросы дэша «Работа с оттоком».

Здесь живёт ТОЛЬКО то, чего нет у дэша tb_health: помесячная история оттока и
возвратов. Всё остальное (организации, задачи, обещания вернуть, портфель по
единицам) читает `tb_health.bank.load` — второй раз ходить в те же таблицы ради
того же среза незачем, а расхождение между двумя отчётами по одним и тем же
числам было бы хуже любой экономии.

Грейн истории — (ГОСБ, месяц, сегмент). Из него сворачиваются все разрезы
отчёта: динамика по месяцам, матрица «единица × сегмент» и итоги ТБ. Уровень ТБ
не запрашивается отдельно: отток — это факт по клиенту, он лежит на ГОСБ, и
сумма ГОСБ здесь и есть ТБ (в отличие от метрик витрины, где уровни считаются
независимо друг от друга).
"""
from ..tb_health.queries import _GMAP

# Порог по строке оттока тот же, что в tb_health: уход одного-двух человек —
# текучка, а не потеря клиента. Один порог на два отчёта обязателен, иначе их
# числа по одному и тому же месяцу разошлись бы.
OUTFLOW_HISTORY = """
WITH gmap AS (""" + _GMAP + """)
SELECT g.new_gosb_id                                           AS unit_id,
       g.tb_id,
       f.report_dt,
       f.segment_name                                          AS seg_name,
       count(DISTINCT f.inn)                                   AS n_orgs,
       sum(f.outflow_qty)                                      AS out_qty,
       sum(COALESCE(r.return_qty, 0))                          AS ret_qty,
       sum(GREATEST(f.outflow_qty - COALESCE(r.return_qty, 0), 0)) AS out_kept,
       sum(CASE WHEN f.is_force THEN f.outflow_qty ELSE 0 END) AS force_qty,
       sum(COALESCE(r.return_qty, 0))
           FILTER (WHERE r.is_outflow_return_success)          AS ret_success_qty
FROM {schema}.uzp_dwh_fact_outflow f
JOIN gmap g ON g.old_gosb_id = f.gosb_id
LEFT JOIN {schema}.uzp_data_outflow_return_detail r
       ON r.report_dt = f.report_dt AND r.gosb_id = f.gosb_id AND r.inn = f.inn
WHERE f.outflow_qty >= :out_min
  AND f.report_dt BETWEEN :hist_from AND :ref_closed
GROUP BY g.new_gosb_id, g.tb_id, f.report_dt, f.segment_name
"""
