// Access vs echo (Track B, T1/T2): one row per (organisation, echoed amendment) with whether any tabling MEP
// has a recorded meeting with the organisation. Baseline computed in s10_analyse.py.
// Label: meetings recorded in 2020 and Jun-Oct 2024 only.
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment {track:'B'})<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false)
WITH DISTINCT o, a
MATCH (m:MEP)-[:TABLED]->(a)
WITH o, a, collect(m.mep_id) AS tablers
OPTIONAL MATCH (o)-[mw:MET_WITH]->(m2:MEP) WHERE m2.mep_id IN tablers
RETURN o.org_id AS org_id, o.user_type AS user_type, a.am_id AS am_id, tablers, count(mw) > 0 AS carrier_met,
       collect(DISTINCT mw.period) AS periods;
