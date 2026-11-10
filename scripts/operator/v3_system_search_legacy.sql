-- Frozen pre-optimization SELECT from PR #700; comparison only, never a writer.
WITH target AS MATERIALIZED (
    SELECT v.system_id64,v.loaded_body_count,v.completeness,v.confidence
      FROM v3_derived.system_rating_vector v
     WHERE v.derived_generation_id=%(generation_id)s AND v.chunk_ordinal=%(chunk_ordinal)s
),
body_summary AS (
    SELECT b.system_id64,
           (count(*) FILTER (
               WHERE b.lifecycle_state='ACTIVE' AND b.is_landable IS TRUE
           ))::integer AS landable_count,
           (count(*) FILTER (
               WHERE b.lifecycle_state='ACTIVE' AND b.is_landable IS TRUE
                 AND (b.atmosphere_classification_id IS NULL OR atmo.public_code='no_atmosphere')
           ))::integer AS walkable_count,
           bool_or(
               b.lifecycle_state='ACTIVE'
               AND ts.public_code IN ('terraformable','terraformed','terraforming')
           ) AS has_terraformable
      FROM {schema}.bodies b
      JOIN target t ON t.system_id64=b.system_id64
 LEFT JOIN v3_vocab.terraforming_state ts
        ON ts.terraforming_state_id=b.terraforming_state_id
 LEFT JOIN v3_vocab.atmosphere_classification atmo
        ON atmo.atmosphere_classification_id=b.atmosphere_classification_id
  GROUP BY b.system_id64
),
type_summary AS (
    SELECT nk.system_id64,
           (count(*) FILTER (WHERE nk.name IN ('earth like world','earthlike world','elw')))::integer AS elw_count,
           (count(*) FILTER (WHERE nk.name IN ('water world','ww')))::integer AS ww_count,
           (count(*) FILTER (WHERE nk.name IN ('ammonia world','ammonia')))::integer AS ammonia_count,
           (count(*) FILTER (WHERE nk.terraformable IS TRUE))::integer AS terraformable_count,
           (count(*) FILTER (WHERE nk.name LIKE '%%gas giant%%'))::integer AS gas_giant_count,
           (count(*) FILTER (WHERE nk.name IN ('high metal content world','high metal content body','high metal content','hmc')))::integer AS hmc_count,
           (count(*) FILTER (WHERE nk.name IN ('metal rich body','metal rich')))::integer AS metal_rich_count,
           (count(*) FILTER (WHERE nk.name IN ('rocky body','rocky')))::integer AS rocky_count,
           (count(*) FILTER (WHERE nk.name IN ('rocky ice body','rocky ice world','rocky ice')))::integer AS rocky_ice_count,
           (count(*) FILTER (WHERE nk.name IN ('icy body','icy')))::integer AS icy_count,
           (count(*) FILTER (WHERE nk.name LIKE '%%black hole%%' OR nk.spectral_class IN ('H','SupermassiveBlackHole')))::integer AS black_hole_count,
           (count(*) FILTER (WHERE nk.name='neutron star' OR nk.spectral_class='N'))::integer AS neutron_count,
           (count(*) FILTER (WHERE nk.name LIKE '%%white dwarf%%' OR nk.spectral_class LIKE 'D%%'))::integer AS white_dwarf_count,
           (count(*) FILTER (
               WHERE nk.spectral_class IS NOT NULL
                 AND NOT (COALESCE(nk.name LIKE '%%black hole%%', false) OR nk.spectral_class IN ('H','SupermassiveBlackHole'))
                 AND NOT (COALESCE(nk.name='neutron star', false) OR nk.spectral_class='N')
                 AND NOT (COALESCE(nk.name LIKE '%%white dwarf%%', false) OR nk.spectral_class LIKE 'D%%')
           ))::integer AS other_star_count
      FROM (
          SELECT bm.system_id64,bm.terraformable,bm.spectral_class,
                 lower(btrim(regexp_replace(regexp_replace(bm.body_class,'[-]',' ','g'),'\s+',' ','g'))) AS name
            FROM v3_derived.body_mechanics bm
           WHERE bm.derived_generation_id=%(generation_id)s
      ) nk
      JOIN target t ON t.system_id64=nk.system_id64
  GROUP BY nk.system_id64
),
ring_summary AS (
    SELECT r.system_id64,
           (count(*) FILTER (WHERE r.lifecycle_state='ACTIVE' AND r.kind='RING'))::integer AS ring_count
      FROM {schema}.rings r
      JOIN target t ON t.system_id64=r.system_id64
  GROUP BY r.system_id64
),
signal_summary AS (
    SELECT b.system_id64,
           sum(bs.signal_count) FILTER (WHERE st.public_code='saa_signaltype_biological')::integer AS bio_signal_total,
           sum(bs.signal_count) FILTER (WHERE st.public_code='saa_signaltype_geological')::integer AS geo_signal_total
      FROM {schema}.body_signal_current bs
      JOIN {schema}.bodies b ON b.body_pk=bs.body_pk
      JOIN target t ON t.system_id64=b.system_id64
      JOIN v3_vocab.signal_type st ON st.signal_type_id=bs.signal_type_id
     WHERE b.lifecycle_state='ACTIVE'
  GROUP BY b.system_id64
),
station_summary AS (
    SELECT st.system_id64,
           (count(*) FILTER (WHERE st.lifecycle_state='ACTIVE'))::integer
               AS station_count
      FROM {schema}.stations st
      JOIN target t ON t.system_id64=st.system_id64
  GROUP BY st.system_id64
),
main_star AS (
    SELECT DISTINCT ON (bm.system_id64)
           bm.system_id64,bm.body_class AS main_star_class
      FROM v3_derived.body_mechanics bm
      JOIN target t ON t.system_id64=bm.system_id64
     WHERE bm.derived_generation_id=%(generation_id)s AND bm.is_main_star IS TRUE
  ORDER BY bm.system_id64,bm.body_pk
)
SELECT %(generation_id)s,t.system_id64,s.name,s.x_ly,s.y_ly,s.z_ly,
       cube(ARRAY[s.x_ly,s.y_ly,s.z_ly]),
       s.galaxy_region_id,gr.display_name,ms.main_star_class,
       t.loaded_body_count,COALESCE(bs.landable_count,0),
       COALESCE(ss.station_count,0),COALESCE(rs.ring_count,0)>0,
       COALESCE(sig.bio_signal_total,0)>0,
       COALESCE(sig.geo_signal_total,0)>0,
       COALESCE(bs.has_terraformable,false),
       s.source_updated_at,
       (SELECT min(value)::double precision/10000
          FROM unnest(t.completeness) AS value),
       (SELECT min(value)::double precision/10000
          FROM unnest(t.confidence) AS value),
       COALESCE(ty.elw_count,0),COALESCE(ty.ww_count,0),COALESCE(ty.ammonia_count,0),
       COALESCE(ty.terraformable_count,0),COALESCE(ty.gas_giant_count,0),
       COALESCE(ty.hmc_count,0),COALESCE(ty.metal_rich_count,0),COALESCE(ty.rocky_count,0),
       COALESCE(ty.rocky_ice_count,0),COALESCE(ty.icy_count,0),
       COALESCE(ty.black_hole_count,0),COALESCE(ty.neutron_count,0),
       COALESCE(ty.white_dwarf_count,0),COALESCE(ty.other_star_count,0),
       COALESCE(rs.ring_count,0),COALESCE(bs.walkable_count,0),
       COALESCE(sig.bio_signal_total,0),COALESCE(sig.geo_signal_total,0)
  FROM target t
  JOIN {schema}.systems s ON s.id64=t.system_id64
         LEFT JOIN v3_vocab.galaxy_region gr
    ON gr.galaxy_region_id=s.galaxy_region_id
         LEFT JOIN body_summary bs USING(system_id64)
         LEFT JOIN type_summary ty USING(system_id64)
         LEFT JOIN ring_summary rs USING(system_id64)
         LEFT JOIN signal_summary sig USING(system_id64)
         LEFT JOIN station_summary ss USING(system_id64)
         LEFT JOIN main_star ms USING(system_id64)
ORDER BY t.system_id64
