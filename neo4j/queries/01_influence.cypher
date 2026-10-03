// Influence score per organisation (Track B): sum over Organisation->Comment<-Chunk-[ECHOED_IN]->Amendment
// of score x tier weight x (1 + survived + reached_law). Judged, relevant echoes only (T1/T2, not hidden).
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment {track:'B'})<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false)
WITH o, a, e,
     CASE e.tier WHEN 'T1' THEN 3.0 WHEN 'T2' THEN 2.0 ELSE 1.0 END AS w,
     1 + CASE WHEN a.survived THEN 1 ELSE 0 END + CASE WHEN a.reached_law THEN 1 ELSE 0 END AS reach
WITH o, sum(e.score * w * reach) AS influence, count(e) AS echoes, count(DISTINCT a) AS amendments,
     count(DISTINCT CASE WHEN a.survived THEN a END) AS amendments_survived,
     count(DISTINCT CASE WHEN a.reached_law THEN a END) AS amendments_reached_law,
     sum(CASE WHEN e.verbatim THEN 1 ELSE 0 END) AS verbatim_echoes
SET o.influence_b = influence
RETURN o.org_id AS org_id, o.name AS name, o.user_type AS user_type, round(influence, 2) AS influence, echoes,
       amendments, amendments_survived, amendments_reached_law, verbatim_echoes
ORDER BY influence DESC;
