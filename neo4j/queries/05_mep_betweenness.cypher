// Brokers (GDS betweenness, undirected): MEPs between organisations (via echoing amendments they tabled) and
// the adopted text (via SURVIVED_AS).
CALL gds.graph.drop('brokers', false) YIELD graphName RETURN graphName;
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)<-[:TABLED]-(m:MEP)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false)
WITH collect(DISTINCT {s: o, t: m}) AS r1
MATCH (m2:MEP)-[:TABLED]->(:Amendment)-[:SURVIVED_AS]->(x:AdoptedAmendment)
WITH r1, collect(DISTINCT {s: m2, t: x}) AS r2
WITH r1 + r2 AS rows
UNWIND rows AS r
WITH gds.graph.project('brokers', r.s, r.t, {}, {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.graphName AS graph, g.nodeCount AS nodes, g.relationshipCount AS rels;
CALL gds.betweenness.stream('brokers') YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score WHERE n:MEP AND score > 0
OPTIONAL MATCH (n)-[:MEMBER_OF]->(g:Group)
RETURN n.name AS mep, collect(DISTINCT g.group_id) AS groups, round(score, 1) AS betweenness
ORDER BY betweenness DESC;
