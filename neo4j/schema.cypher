// ======================================================================
// REVERSA AI - GRAPH SCHEMA DEFINITION (Neo4j)
// Structure: Organization, Feedback, Chunk, MEP, PoliticalGroup, 
//            Amendment, LawProvision + Relationships
// ======================================================================

// ----------------------------------------------------------------------
// 1. UNIQUE CONSTRAINTS (Primary Keys)
// ----------------------------------------------------------------------

// 🏢 Organization: Transparency Register ID (e.g., "56047191389-84" or slug)
CREATE CONSTRAINT org_id_unique IF NOT EXISTS
FOR (o:Organization) REQUIRE o.org_id IS UNIQUE;

// 🗣️ Feedback: Unique consultation submission ID (from HaveYourSay API)
CREATE CONSTRAINT feedback_id_unique IF NOT EXISTS
FOR (f:Feedback) REQUIRE f.feedback_id IS UNIQUE;

// 🧩 Chunk: UUID for atomic text segments
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE;

// 🏛️ MEP: European Parliament Member unique ID (from ParlTrack/IntegrityWatch)
CREATE CONSTRAINT mep_id_unique IF NOT EXISTS
FOR (m:MEP) REQUIRE m.ep_id IS UNIQUE;

// 🟡 PoliticalGroup: Political group acronym (EPP, S&D, Renew, Greens/EFA, ID, ECR, The Left, NI)
CREATE CONSTRAINT group_id_unique IF NOT EXISTS
FOR (g:PoliticalGroup) REQUIRE g.group_id IS UNIQUE;

// 📝 Amendment: Unique parliamentary amendment ID
CREATE CONSTRAINT am_id_unique IF NOT EXISTS
FOR (a:Amendment) REQUIRE a.am_id IS UNIQUE;

// ⚖️ LawProvision: Hierarchical legal unit ID (e.g., "Art. 5(1)(a)", "Recital 12", "Annex III")
CREATE CONSTRAINT law_unit_id_unique IF NOT EXISTS
FOR (lp:LawProvision) REQUIRE lp.unit_id IS UNIQUE;


// ----------------------------------------------------------------------
// 2. SEARCH & LOOKUP INDEXES (Performance Optimization)
// ----------------------------------------------------------------------

// Organization indexes
CREATE INDEX org_name_idx IF NOT EXISTS
FOR (o:Organization) ON (o.name);

CREATE INDEX org_user_type_idx IF NOT EXISTS
FOR (o:Organization) ON (o.user_type);

CREATE INDEX org_country_idx IF NOT EXISTS
FOR (o:Organization) ON (o.country);

// Feedback indexes
CREATE INDEX feedback_pub_id_idx IF NOT EXISTS
FOR (f:Feedback) ON (f.publication_id);

CREATE INDEX feedback_stage_name_idx IF NOT EXISTS
FOR (f:Feedback) ON (f.stage_name);

CREATE INDEX feedback_date_idx IF NOT EXISTS
FOR (f:Feedback) ON (f.date);

// Chunk indexes
CREATE INDEX chunk_source_type_idx IF NOT EXISTS
FOR (c:Chunk) ON (c.source_type);

// MEP indexes
CREATE INDEX mep_name_idx IF NOT EXISTS
FOR (m:MEP) ON (m.name);

CREATE INDEX mep_country_idx IF NOT EXISTS
FOR (m:MEP) ON (m.country);

// Amendment indexes
CREATE INDEX amendment_committee_idx IF NOT EXISTS
FOR (a:Amendment) ON (a.committee);

CREATE INDEX amendment_date_idx IF NOT EXISTS
FOR (a:Amendment) ON (a.date);

CREATE INDEX amendment_reached_law_idx IF NOT EXISTS
FOR (a:Amendment) ON (a.reached_law);

// LawProvision indexes
CREATE INDEX law_provision_is_final_idx IF NOT EXISTS
FOR (lp:LawProvision) ON (lp.is_final_law);


// ----------------------------------------------------------------------
// 3. GRAPH DATA MODEL SPECIFICATION (Documentation & Contract)
// ----------------------------------------------------------------------
//
// NODES & PROPERTIES:
// - (:Organization {
//       org_id: String [UNIQUE],
//       name: String,
//       user_type: String,         // 'COMPANY', 'BUSINESS_ASSOCIATION', 'NGO', 'CONSUMER_ORGANISATION', 'TRADE_UNION'
//       country: String,
//       lobbying_costs_eur: Float, // from LobbyFacts
//       fte: Float,                // Full-time equivalents from LobbyFacts
//       ep_passes: Integer         // EP accreditation passes from LobbyFacts
//   })
//
// - (:Feedback {
//       feedback_id: Integer [UNIQUE],
//       publication_id: Integer,   // 13340 (Stage 1 Inception) or 14488 (Stage 2 Proposal)
//       stage_name: String,        // 'STAGE_1_INCEPTION_FEEDBACK' or 'STAGE_2_PROPOSAL_FEEDBACK'
//       date: Date,
//       organization_name: String,
//       country: String
//   })
//
// - (:Chunk {
//       chunk_id: String [UNIQUE], // UUID
//       text: String,
//       source_type: String,       // 'form_text' or 'attachment'
//       order: Integer,
//       embedding: List<Float>     // 384 or 1536 float vector
//   })
//
// - (:MEP {
//       ep_id: String [UNIQUE],
//       name: String,
//       country: String
//   })
//
// - (:PoliticalGroup {
//       group_id: String [UNIQUE]  // 'EPP', 'S&D', 'Renew', 'Greens/EFA', 'ECR', 'ID', 'The Left', 'NI'
//   })
//
// - (:Amendment {
//       am_id: String [UNIQUE],
//       date: Date,
//       committee: String,         // 'IMCO', 'LIBE', 'JURI', 'ITRE', etc.
//       old_text: String,
//       new_text: String,
//       reached_law: Boolean
//   })
//
// - (:LawProvision {
//       unit_id: String [UNIQUE],  // e.g. "Art. 5(1)(a)"
//       text: String,
//       is_final_law: Boolean      // True = final AI Act, False = Commission proposal
//   })
//
// RELATIONSHIPS:
// - (:Organization)-[:SUBMITTED]->(:Feedback)
// - (:Chunk)-[:PART_OF {order: Integer}]->(:Feedback)
// - (:Chunk)-[:ECHOED_IN {
//       score: Float, 
//       tier: String,              // 'T1', 'T2'
//       llm_direction: String,     // 'supports', 'opposes'
//       overlap_quote: String
//   }]->(:Amendment)
// - (:Organization)-[:MET_WITH {
//       date: Date, 
//       dossier: String, 
//       location: String
//   }]->(:MEP)
// - (:MEP)-[:TABLED {is_lead_author: Boolean}]->(:Amendment)
// - (:MEP)-[:MEMBER_OF]->(:PoliticalGroup)
// - (:Amendment)-[:TARGETS]->(:LawProvision)
// - (:Amendment)-[:SURVIVED_AS {survival_score: Float}]->(:LawProvision)
// - (:Chunk)-[:SIMILAR_TO {score: Float}]->(:Chunk)
// - (:Chunk)-[:TOOK_POSITION {stance: String, proposal_choice: String}]->(:LawProvision)
// ======================================================================
