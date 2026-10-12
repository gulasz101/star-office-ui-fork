-- SOU initial schema (ADR-034) - agents, events, sessions.
--
-- GENERATED ARTIFACT for reading and review only. The migration is the source
-- of truth: backend/migrations/versions/001_initial_schema.py
--
-- Reproduce both sections from backend/:
--   DATABASE_URL=postgresql+psycopg2://sou@127.0.0.1:55432/sou alembic upgrade base:head --sql
--   alembic upgrade base:head --sql
--
-- Alembic's own bookkeeping table (alembic_version) is omitted here.

-- ===========================================================================
-- PostgreSQL (primary backend, homelab k3s / CloudNativePG)
-- SERIAL row ids, JSONB payload, DESC-capable btree indexes
-- ===========================================================================
-- Running upgrade  -> 001_initial_schema

CREATE TABLE agents (
    id SERIAL NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    agent_type VARCHAR(50) NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (id)
);

CREATE TABLE events (
    id SERIAL NOT NULL, 
    agent_id INTEGER NOT NULL, 
    event_type VARCHAR(100) NOT NULL, 
    payload JSONB NOT NULL, 
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT fk_events_agent_id FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX idx_events_agent_created ON events (agent_id, created_at DESC);

CREATE TABLE sessions (
    id SERIAL NOT NULL, 
    agent_id INTEGER NOT NULL, 
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
    ended_at TIMESTAMP WITHOUT TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT fk_sessions_agent_id FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX idx_sessions_agent_started ON sessions (agent_id, started_at DESC);

-- ===========================================================================
-- SQLite (fallback backend, local dev)
-- INTEGER PRIMARY KEY AUTOINCREMENT, TEXT payload holding the JSON string,
-- CURRENT_TIMESTAMP default; the same indexes and cascade rules
-- ===========================================================================
-- Running upgrade  -> 001_initial_schema

CREATE TABLE agents (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
    name VARCHAR(255) NOT NULL, 
    agent_type VARCHAR(50) NOT NULL, 
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
);

CREATE TABLE events (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
    agent_id INTEGER NOT NULL, 
    event_type VARCHAR(100) NOT NULL, 
    payload TEXT NOT NULL, 
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
    CONSTRAINT fk_events_agent_id FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX idx_events_agent_created ON events (agent_id, created_at DESC);

CREATE TABLE sessions (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
    agent_id INTEGER NOT NULL, 
    started_at DATETIME NOT NULL, 
    ended_at DATETIME, 
    CONSTRAINT fk_sessions_agent_id FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE
);

CREATE INDEX idx_sessions_agent_started ON sessions (agent_id, started_at DESC);
