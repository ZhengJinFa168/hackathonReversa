// Centrality (GDS PageRank) on Organisation -> Amendment -> Provision: influential organisations and
// contested provisions. Echo edges weighted by tier. Undirected: organisations are sources in the directed
// graph and would all get the base score.
CALL gds.graph.drop('influence', false) YIELD graphName RETURN graphName;
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false)
WITH o, a, sum(CASE e.tier WHEN 'T1' THEN 3.0 ELSE 2.0 END) AS w
WITH collect({s: o, t: a, w: w}) AS rows1
MATCH (a2:Amendment)-[:AMENDS]->(p:Provision)
WITH rows1, collect({s: a2, t: p, w: 1.0}) AS rows2
WITH rows1 + rows2 AS rows
UNWIND rows AS r
WITH gds.graph.project('influence', r.s, r.t, {relationshipProperties: {w: r.w}}, {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.graphName AS graph, g.nodeCount AS nodes, g.relationshipCount AS rels;
CALL gds.pageRank.stream('influence', {relationshipWeightProperty: 'w', maxIterations: 50})
YIELD nodeId, score
WITH gds.util.asNode(nodeId) AS n, score
WHERE n:Organisation OR n:Provision
RETURN labels(n)[0] AS label, coalesce(n.name, n.unit_id) AS node, n.user_type AS user_type, round(score, 4) AS pagerank
ORDER BY score DESC;
