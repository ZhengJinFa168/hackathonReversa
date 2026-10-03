// Battlegrounds: provisions whose amendments echo different user types in opposite directions
// (one side asks for stricter rules, the other for looser ones).
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)-[:AMENDS]->(p:Provision)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false) AND e.llm_direction IN ['strengthens','weakens']
WITH split(p.unit_id, '(')[0] AS provision, e.llm_direction AS direction, o.user_type AS user_type, count(*) AS n
WITH provision, collect({direction: direction, user_type: user_type, n: n}) AS sides
WITH provision, sides,
     [s IN sides WHERE s.direction = 'strengthens' | s.user_type] AS stricter,
     [s IN sides WHERE s.direction = 'weakens' | s.user_type] AS looser
WHERE any(u1 IN stricter WHERE any(u2 IN looser WHERE u1 <> u2))  // opposite directions from different user types
RETURN provision,
       reduce(t = 0, s IN sides | t + s.n) AS echoes,
       [s IN sides WHERE s.direction = 'strengthens' | s.user_type + ':' + toString(s.n)] AS stricter_by,
       [s IN sides WHERE s.direction = 'weakens' | s.user_type + ':' + toString(s.n)] AS looser_by
ORDER BY echoes DESC;
