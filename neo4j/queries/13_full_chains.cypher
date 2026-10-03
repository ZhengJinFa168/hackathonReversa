// The full chain (Track B): organisation -> comment -> chunk -> echo -> amendment -> MEP/group -> adopted -> law.
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment)<-[:PART_OF]-(c:Chunk)
      -[e:ECHOED_IN]->(a:Amendment)<-[:TABLED {is_lead_author:true}]-(m:MEP)
WHERE e.tier IN ['T1','T2'] AND NOT coalesce(e.hidden, false) AND a.reached_law
OPTIONAL MATCH (a)-[:SURVIVED_AS]->(x:AdoptedAmendment)-[:BECAME]->(l:LegalUnit)
OPTIONAL MATCH (m)-[:MEMBER_OF]->(g:Group)
RETURN o.name AS organisation, o.user_type AS user_type, o.lobbying_cost_eur AS lobbying_cost_eur, a.am_id AS amendment,
       a.location AS location, m.name AS lead_mep, collect(DISTINCT g.group_id) AS groups, x.adopted_id AS adopted,
       l.law_unit_id AS law_unit, e.tier AS tier, round(e.score, 2) AS score, e.overlap_quote AS quote, e.carrier_met AS carrier_met
ORDER BY tier, score DESC LIMIT 100;
