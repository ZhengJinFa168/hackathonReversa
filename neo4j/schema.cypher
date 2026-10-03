// Uniqueness constraints per node key (docs/IMPLEMENTATION_PLAN.md §1). Idempotent.
CREATE CONSTRAINT organisation_key IF NOT EXISTS FOR (n:Organisation) REQUIRE n.org_id IS UNIQUE;
CREATE CONSTRAINT comment_key IF NOT EXISTS FOR (n:Comment) REQUIRE n.feedback_id IS UNIQUE;
CREATE CONSTRAINT chunk_key IF NOT EXISTS FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE;
CREATE CONSTRAINT provision_key IF NOT EXISTS FOR (n:Provision) REQUIRE n.unit_id IS UNIQUE;
CREATE CONSTRAINT amendment_key IF NOT EXISTS FOR (n:Amendment) REQUIRE n.am_id IS UNIQUE;
CREATE CONSTRAINT adopted_key IF NOT EXISTS FOR (n:AdoptedAmendment) REQUIRE n.adopted_id IS UNIQUE;
CREATE CONSTRAINT legalunit_key IF NOT EXISTS FOR (n:LegalUnit) REQUIRE n.law_unit_id IS UNIQUE;
CREATE CONSTRAINT mep_key IF NOT EXISTS FOR (n:MEP) REQUIRE n.mep_id IS UNIQUE;
CREATE CONSTRAINT group_key IF NOT EXISTS FOR (n:Group) REQUIRE n.group_id IS UNIQUE;
CREATE CONSTRAINT committee_key IF NOT EXISTS FOR (n:Committee) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT issue_key IF NOT EXISTS FOR (n:Issue) REQUIRE n.issue_id IS UNIQUE;

// Lookup indexes
CREATE INDEX organisation_tr IF NOT EXISTS FOR (n:Organisation) ON (n.tr_id);
CREATE INDEX organisation_type IF NOT EXISTS FOR (n:Organisation) ON (n.user_type);
CREATE INDEX comment_track IF NOT EXISTS FOR (n:Comment) ON (n.track);
CREATE INDEX amendment_reached IF NOT EXISTS FOR (n:Amendment) ON (n.reached_law);
CREATE INDEX provision_article IF NOT EXISTS FOR (n:Provision) ON (n.article);
CREATE INDEX echo_tier IF NOT EXISTS FOR ()-[r:ECHOED_IN]-() ON (r.tier);
CREATE INDEX echo_run IF NOT EXISTS FOR ()-[r:ECHOED_IN]-() ON (r.run_id);

// Optional: similar chunks inside Neo4j for the demo (multilingual-e5-base = 768 dims). S05 fills Chunk.embedding.
CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON (c.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};
