// Live demo in Neo4j Browser: paste one query at a time (graph view). Echoes shown are judged T1/T2.

// 1. One full chain: Twilio's push for the OECD AI definition -> amendments -> report -> final Art 3(1)(1)
MATCH p = (o:Organisation {name:'Twilio'})-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)
          -[e:ECHOED_IN]->(a:Amendment)-[:SURVIVED_AS]->(:AdoptedAmendment)-[:BECAME]->(:LegalUnit)
WHERE e.tier IN ['T1','T2']
OPTIONAL MATCH q = (m:MEP)-[:TABLED {is_lead_author:true}]->(a)
RETURN p, q LIMIT 10;

// 2. Avaaz and the emotion-recognition ban: verbatim echo in Art 5 amendments, who tabled them, where they went
MATCH p = (o:Organisation {name:'Avaaz Foundation'})-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c:Chunk)-[e:ECHOED_IN {verbatim:true}]->(a:Amendment)<-[:TABLED]-(m:MEP)-[:MEMBER_OF]->(:Group)
RETURN p LIMIT 25;

// 3. Access: organisations that met an MEP who then tabled an amendment echoing them (carrier_met)
MATCH p = (o:Organisation)-[mw:MET_WITH]->(m:MEP)-[:TABLED]->(a:Amendment)<-[e:ECHOED_IN]-(c:Chunk)-[:PART_OF]->(:Comment)<-[:SUBMITTED]-(o)
WHERE e.tier IN ['T1','T2'] AND e.carrier_met IS NOT NULL
RETURN p LIMIT 25;

// 4. Coalition: the digital-rights NGOs sharing text (SIMILAR_TO) and echoing the same amendments
MATCH (o:Organisation) WHERE o.name IN ['European Digital Rights (EDRi)', 'Digitalcourage e.V.', 'Access Now Europe', 'Bits of Freedom']
MATCH p = (o)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(:Chunk)-[e:ECHOED_IN]->(a:Amendment)
WHERE e.tier IN ['T1','T2']
RETURN p LIMIT 60;

// 5. Similar chunks to a given paragraph (vector index), across organisations
MATCH (c:Chunk {chunk_id:'2665651:3'})
CALL db.index.vector.queryNodes('chunk_embedding', 10, c.embedding) YIELD node, score
MATCH (node)-[:PART_OF]->(:Comment)<-[:SUBMITTED]-(o:Organisation)
RETURN o.name, round(score, 3) AS sim, left(node.text, 160) AS text;
