// Which MEPs and roles met the most consultation respondents, and in which group (Integrity Watch;
// recorded meetings 2020 and Jun-Oct 2024 only). tabled_ai_act = the MEP tabled AI Act committee amendments.
MATCH (o:Organisation)-[r:MET_WITH]->(m:MEP)
WITH m, r.mep_role AS role, count(DISTINCT o) AS respondents, count(r) AS meetings,
     sum(CASE WHEN r.ai_related THEN 1 ELSE 0 END) AS ai_meetings
OPTIONAL MATCH (m)-[:TABLED]->(a:Amendment)
RETURN m.name AS mep, coalesce(m.iw_group, '') AS group, role, respondents, meetings, ai_meetings,
       count(a) > 0 AS tabled_ai_act
ORDER BY respondents DESC, meetings DESC;
