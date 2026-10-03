// Track A context: AI-related MEP meetings of inception-consultation respondents in 2020 (consultation ran
// Jul-Sep 2020).
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment {track:'A'})
MATCH (o)-[r:MET_WITH {period:'2020', ai_related:true}]->(m:MEP)
RETURN o.name AS organisation, o.user_type AS user_type, m.name AS mep, r.mep_role AS role, r.date AS date, r.title AS title
ORDER BY date;
