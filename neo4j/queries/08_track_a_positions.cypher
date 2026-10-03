// Track A stance: every coded position with the proposal's choice (alignment stats and baseline in s10_analyse.py).
MATCH (o:Organisation)-[r:TOOK_POSITION]->(i:Issue)
RETURN o.org_id AS org_id, o.name AS organisation, o.user_type AS user_type, i.issue_id AS issue, r.stance AS stance,
       i.proposal_choice AS proposal_choice, r.aligned AS aligned, r.quote AS quote
ORDER BY issue, user_type;
