-- Выгрузка ЦОССУ из proon / схема social_proon.
-- Колонки идут строго в том порядке, который ждёт worker/seed_cossu.py (1..18).
-- Вместо E'\n' / E'\r' используются chr(10) / chr(13) — это ровно то же самое,
-- но в файле нет обратных слэшей, которые могут пострадать при передаче.
WITH approved_by_org AS (
    SELECT
        smla.organization_bin::text AS bin,
        BOOL_OR(smla.current_status_id = 5) AS has_approved
    FROM s_msu_license_application smla
    GROUP BY smla.organization_bin::text
),
     branch_base AS (
         SELECT DISTINCT ON (smb.id)
             smb.id AS branch_id,
             dk.te AS dk_te,
             dot.name_ru AS sobst,
             dk_region.name_ru AS region,
             dk_region.ab AS kato_region,
             CASE
                 WHEN dk_region.name_ru IN ('г.Астана', 'г.Алматы', 'г.Шымкент') THEN dk.name_ru
                 ELSE dk_rayon.name_ru
                 END AS rayon,
             CASE
                 WHEN dk_region.name_ru IN ('г.Астана', 'г.Алматы', 'г.Шымкент') THEN CONCAT(dk.ab, dk.cd)
                 ELSE CONCAT(dk_rayon.ab, dk_rayon.cd)
                 END AS kato_rayon,
             CASE
                 WHEN dk_region.name_ru IN ('г.Астана', 'г.Алматы', 'г.Шымкент') THEN NULL
                 ELSE dk.name_ru
                 END AS rayon2,
             CASE
                 WHEN dk_region.name_ru IN ('г.Астана', 'г.Алматы', 'г.Шымкент') THEN NULL
                 ELSE CONCAT(dk.ab, dk.cd, dk.ef)
                 END AS kato_rayon2,
             COALESCE(NULLIF(REPLACE(TRIM(sreom.additional_address_ru), chr(10), ' '), ''), '') AS additional_address_ru,
             CONCAT_WS(', ',
                       NULLIF(REPLACE(REPLACE(TRIM(dk_region.name_ru), chr(13), ' '), chr(10), ' '), ''),
                       CASE
                           WHEN dk_region.name_ru IN ('г.Астана', 'г.Алматы', 'г.Шымкент')
                               THEN NULLIF(REPLACE(REPLACE(TRIM(dk.name_ru), chr(13), ' '), chr(10), ' '), '')
                           ELSE NULLIF(REPLACE(REPLACE(TRIM(dk_rayon.name_ru), chr(13), ' '), chr(10), ' '), '')
                           END,
                       CASE
                           WHEN dk_okrug.id IS NOT NULL
                               THEN NULLIF(REPLACE(REPLACE(TRIM(dk_okrug.name_ru), chr(13), ' '), chr(10), ' '), '')
                           ELSE NULL
                           END,
                       CASE
                           WHEN dk.id IS NOT NULL
                               AND dk.id IS DISTINCT FROM dk_rayon.id
                               AND dk.id IS DISTINCT FROM dk_okrug.id
                               AND dk.id IS DISTINCT FROM dk_region.id
                               THEN NULLIF(REPLACE(REPLACE(TRIM(dk.name_ru), chr(13), ' '), chr(10), ' '), '')
                           ELSE NULL
                           END,
                       NULLIF(REPLACE(REPLACE(TRIM(sreom.additional_address_ru), chr(13), ' '), chr(10), ' '), '')
             ) AS fulladdress,
             sjm.bin AS org_bin,
             sjm.fullname_ru AS org_name,
             smb.name_ru AS otd_name,
             (CASE WHEN ssp.name_ru IN ('ЦОССУ ДЛЯ ЖЕРТВ БЫТОВОГО НАСИЛИЯ','ЦОССУ ДЛЯ ЖЕРТВ ТОРГОВЛИ ЛЮДЬМИ')
                       THEN 'ЦОССУ В УСЛОВИЯХ ВРЕМЕННОГО ПРЕБЫВАНИЯ' ELSE ssp.name_ru END)::text AS otd_typ,
             ss.name_ru AS otd_podtyp,
             smb.beds_count AS fakt_koika_mesto,
             CASE
                 WHEN smb.application_id IS NOT NULL THEN 'Да'
                 ELSE 'Нет'
                 END AS zayavka,
             smla.number AS application_number,
             smla.planned_output AS all_count
         FROM s_provider_activities spa
                  JOIN s_juridical_member sjm ON sjm.member_id = spa.member_id AND sjm.actual_date_to > CURRENT_TIMESTAMP
                  LEFT JOIN approved_by_org abo ON abo.bin = sjm.bin::text
                  JOIN d_ownership_type dot ON sjm.ownership_type_id = dot.id AND dot.actual_date_to > CURRENT_TIMESTAMP
                  JOIN s_activity_msus sam ON sam.activity_id = spa.id
                  JOIN s_msu_branches smb ON smb.activity_msu_id = sam.id AND smb.actual_date_to > CURRENT_TIMESTAMP
                  LEFT JOIN s_msu_license_application smla ON smb.application_id = smla.id
                  JOIN s_services ss ON ss.id = smb.service_id AND ss.actual_date_to > CURRENT_TIMESTAMP
                  JOIN s_services ssp ON ssp.id = ss.parent_id AND ssp.actual_date_to > CURRENT_TIMESTAMP
                  LEFT JOIN s_real_estate_of_member sreom ON sreom.version_id = smb.fact_address_version_id AND sreom.actual_date_to > CURRENT_TIMESTAMP
                  LEFT JOIN d_kato dk ON dk.id = sreom.kato_id AND dk.actual_date_to > CURRENT_TIMESTAMP
                  LEFT JOIN d_kato dk_region ON dk_region.ab = dk.ab AND dk_region.cd = '00' AND dk_region.ef = '00' AND dk_region.hij = '000'
                  LEFT JOIN d_kato dk_rayon ON dk_rayon.ab = dk.ab AND dk_rayon.cd = dk.cd AND dk_rayon.ef = '00' AND dk_rayon.hij = '000'
                  LEFT JOIN d_kato dk_okrug ON dk_okrug.ab = dk.ab AND dk_okrug.cd = dk.cd AND dk_okrug.ef = dk.ef AND dk_okrug.hij = '000' AND dk_okrug.id != dk.id
         WHERE spa.activity_type_id = 1
           AND spa.supplier_status_code = 1
     ),
     residents_events AS (
         SELECT DISTINCT
             smj.consumer_id,
             smj.branch_id,
             smjsh.create_date AS event_dt,
             2 AS src_priority
         FROM s_msu_journal smj
                  JOIN s_msu_journal_status_histories smjsh ON smjsh.id = smj.status_history_id
                  JOIN s_consumers sc ON smj.consumer_id = sc.id
                  JOIN s_physical_member spm ON spm.member_id = sc.member_id AND spm.actual_date_to > CURRENT_TIMESTAMP AND spm.alive IS NOT FALSE
         WHERE smjsh.status_id IN (2, 3, 4, 9)
           AND smj.consumer_id IS NOT NULL
         UNION ALL
         SELECT DISTINCT
             smjcd.other_consumer_id,
             smjcd.branch_id,
             smjsh.create_date AS event_dt,
             1 AS src_priority
         FROM s_msu_journal_come_directly smjcd
                  JOIN s_msu_journal_status_histories smjsh ON smjsh.id = smjcd.status_history_id
                  JOIN s_other_consumers soc ON smjcd.other_consumer_id = soc.id
                  LEFT JOIN s_physical_member spm ON spm.iin = soc.iin AND spm.actual_date_to > CURRENT_TIMESTAMP AND spm.alive IS NOT FALSE
         WHERE smjsh.status_id IN (2, 3, 4, 9)
           AND smjcd.other_consumer_id IS NOT NULL
     ),
     residents_dedup AS (
         SELECT DISTINCT ON (consumer_id)
             consumer_id,
             branch_id
         FROM residents_events
         ORDER BY
             consumer_id,
             event_dt DESC NULLS LAST,
             src_priority DESC
     ),
     residents_cnt AS (
         SELECT
             branch_id,
             COUNT(*) AS residents_count
         FROM residents_dedup
         GROUP BY branch_id
     ),
     queue AS (
         SELECT
             smq.branch_id,
             COUNT(DISTINCT smq.consumer_id) AS queue_count
         FROM branch_base bb
                  JOIN s_order_msu som ON som.branch_id = bb.branch_id
                  JOIN s_msu_queue smq ON smq.id = som.queue_id AND smq.is_active IS TRUE
                  JOIN s_order_msu_status_histories somsh ON somsh.id = som.status_history_id AND somsh.order_msu_status_id <> 7
                  JOIN s_consumers sc2 ON sc2.id = som.consumer_id
                  JOIN s_physical_member spm ON spm.member_id = sc2.member_id AND spm.actual_date_to > CURRENT_TIMESTAMP AND spm.alive IS NOT FALSE
         WHERE smq.is_active IS TRUE
           AND smq.out_of_turn_priority_level NOT IN (30)
         GROUP BY smq.branch_id
     )
SELECT
    STRING_AGG(bb.branch_id::text, ', ' ORDER BY bb.branch_id) AS branch_ids,
    bb.region,
    bb.kato_region,
    bb.rayon,
    bb.kato_rayon,
    bb.rayon2,
    bb.kato_rayon2,
    bb.additional_address_ru,
    bb.org_bin,
    bb.sobst,
    bb.org_name,
    bb.fulladdress,
    bb.otd_name,
    bb.otd_typ,
    bb.otd_podtyp,
    SUM(COALESCE(bb.fakt_koika_mesto, 0)) AS fakt_koika_mesto,
    SUM(COALESCE(rc.residents_count, 0)) AS residents_count,
    SUM(COALESCE(q.queue_count, 0)) AS queue_count
FROM branch_base bb
         LEFT JOIN residents_cnt rc ON rc.branch_id = bb.branch_id
         LEFT JOIN queue q ON q.branch_id = bb.branch_id
WHERE NOT (
    COALESCE(rc.residents_count, 0) = 0
        AND COALESCE(q.queue_count, 0) = 0
        AND bb.zayavka = 'Нет'
    )
  AND COALESCE(bb.fakt_koika_mesto, 0) <> 0
GROUP BY
    bb.region,
    bb.kato_region,
    bb.rayon,
    bb.kato_rayon,
    bb.rayon2,
    bb.kato_rayon2,
    bb.additional_address_ru,
    bb.org_bin,
    bb.sobst,
    bb.org_name,
    bb.fulladdress,
    bb.otd_name,
    bb.otd_typ,
    bb.otd_podtyp,
    bb.zayavka,
    bb.application_number,
    bb.all_count
ORDER BY
    bb.region,
    bb.org_bin;
