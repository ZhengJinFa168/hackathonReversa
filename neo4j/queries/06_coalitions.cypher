// Coalitions (GDS Louvain) on the Organisation-Organisation graph: co-echoed in the same amendment (T1/T2)
// plus near-duplicate chunks (SIMILAR_TO). Compare communities with user_type.
CALL gds.graph.drop('coalitions', false) YIELD graphName RETURN graphName;
MATCH (o1:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)-[e1:ECHOED_IN]->(a:Amendment)<-[e2:ECHOED_IN]-(:Chunk)-[:PART_OF]->(:Comment)<-[:SUBMITTED]-(o2:Organisation)
WHERE elementId(o1) < elementId(o2) AND e1.tier IN ['T1','T2'] AND e2.tier IN ['T1','T2']
  AND NOT coalesce(e1.hidden, false) AND NOT coalesce(e2.hidden, false)
WITH o1, o2, count(DISTINCT a) AS w
WITH collect({s: o1, t: o2, w: toFloat(w)}) AS r1
MATCH (o3:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c1:Chunk)-[:SIMILAR_TO]-(c2:Chunk)-[:PART_OF]->(:Comment)<-[:SUBMITTED]-(o4:Organisation)
WHERE elementId(o3) < elementId(o4)
WITH r1, o3, o4, count(*) AS w2
WITH r1, collect({s: o3, t: o4, w: toFloat(w2) * 2}) AS r2
WITH r1 + r2 AS rows
UNWIND rows AS r
WITH gds.graph.project('coalitions', r.s, r.t, {relationshipProperties: {w: r.w}}, {undirectedRelationshipTypes: ['*']}) AS g
RETURN g.graphName AS graph, g.nodeCount AS nodes, g.relationshipCount AS rels;
CALL gds.louvain.stream('coalitions', {relationshipWeightProperty: 'w', consecutiveIds: true}) YIELD nodeId, communityId
WITH gds.util.asNode(nodeId) AS o, communityId
SET o.coalition = communityId
RETURN communityId AS coalition, o.name AS organisation, o.user_type AS user_type, o.country AS country
ORDER BY coalition, user_type, organisation;
