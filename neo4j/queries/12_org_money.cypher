// Money vs influence input (2026 register figures): every Track B organisation with its register properties and
// influence score; Spearman correlations with bootstrap CIs are computed in s10_analyse.py.
MATCH (o:Organisation)-[:SUBMITTED]->(:Comment {track:'B'})
RETURN DISTINCT o.org_id AS org_id, o.name AS name, o.user_type AS user_type, o.match_method AS match_method,
       o.match_confidence AS match_confidence, coalesce(o.influence_b, 0.0) AS influence,
       o.lobbying_cost_eur AS lobbying_cost_eur, o.fte AS fte, o.ep_passes_all AS ep_passes_all,
       o.ec_meetings_vdl1 AS ec_meetings_vdl1, o.mep_meetings_all AS mep_meetings_all;
