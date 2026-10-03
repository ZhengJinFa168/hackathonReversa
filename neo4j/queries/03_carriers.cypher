// Carriers: which MEPs tabled amendments echoing which organisations (T1/T2), in which group, and whether a
// meeting between them is recorded (Integrity Watch covers 2020 and Jun-Oct 2024 only).
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)<-[t:TABLED]-(m:MEP)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false)
WITH DISTINCT o, m, a, t
WITH o, m, t.group AS grp, count(a) AS amendments, sum(t.weight) AS weighted,
     sum(CASE WHEN a.reached_law THEN 1 ELSE 0 END) AS reached_law
OPTIONAL MATCH (o)-[mw:MET_WITH]->(m)
RETURN o.name AS organisation, o.user_type AS user_type, m.name AS mep, grp AS mep_group, amendments,
       round(weighted, 2) AS weighted_amendments, reached_law, count(mw) AS recorded_meetings
ORDER BY weighted_amendments DESC;
