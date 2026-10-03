// ======================================================================
// REVERSA AI - CORE METRICS QUERIES
// ======================================================================

// 2. Reach Funnel (L'efficacia del lobbying fino alla legge)
// :params { target_user_type: 'NGO' }
MATCH (o:Organization {user_type: 'NGO'})-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(c:Chunk)
OPTIONAL MATCH (c)-[e:ECHOED_IN]->(a:Amendment)
OPTIONAL MATCH (a)-[:SURVIVED_AS]->(lp:LawProvision {is_final_law: true})
RETURN count(DISTINCT c) AS total_chunks, 
       count(DISTINCT e) AS echoes,
       count(DISTINCT lp) AS law_impacts;

// 3. Carriers (Quali MEP/Gruppi "portano" le istanze di chi)
MATCH (o:Organization)-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(:Chunk)-[:ECHOED_IN]->(a:Amendment)<-[:TABLED]-(m:MEP)-[:MEMBER_OF]->(g:PoliticalGroup)
RETURN o.name AS Lobby, m.name AS MEP, g.group_id AS Group, count(a) AS Am_Tabled
ORDER BY Am_Tabled DESC;

// 5. Coalitions (Copiatura di chunk tra lobby)
MATCH (o1:Organization)-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(c1:Chunk)
MATCH (o2:Organization)-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(c2:Chunk)
MATCH (c1)-[s:SIMILAR_TO]->(c2)
WHERE elementId(o1) < elementId(o2) AND s.score > 0.85
RETURN o1.name AS Org1, o1.user_type AS Type1, o2.name AS Org2, o2.user_type AS Type2, s.score AS Similarity
ORDER BY s.score DESC;

// 6. Battlegrounds (Lobby contrapposte sullo stesso emendamento/articolo)
MATCH (o1:Organization)-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(c1:Chunk)
      -[e1:ECHOED_IN {llm_direction: 'supports'}]->(a:Amendment)-[:TARGETS]->(lp:LawProvision)
MATCH (o2:Organization)-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(c2:Chunk)
      -[e2:ECHOED_IN {llm_direction: 'opposes'}]->(a)
WHERE o1.user_type <> o2.user_type
RETURN lp.unit_id AS Article, o1.user_type AS Proponent, o2.user_type AS Opponent;

// 7. Money vs Influence (Correlazione risorse e successo)
MATCH (o:Organization)
OPTIONAL MATCH (o)-[:SUBMITTED]->(:Feedback)<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)
WITH o, count(e) as influence_score
RETURN o.name AS Organization, o.lobbying_costs_eur AS Budget, o.fte AS FTE, o.ep_passes AS Passes, influence_score
ORDER BY influence_score DESC;
