-- Databases the Oasis Platform needs alongside the CASS control plane.
--
-- Postgres runs everything in /docker-entrypoint-initdb.d once, when the data
-- directory is first initialised. That is the right moment for this: creating
-- these databases is part of standing an installation up, not something to
-- repeat on every restart.
--
-- Two separate databases rather than shared schemas in one. The Oasis server
-- owns its own migrations and the celery result backend is written by workers
-- on a different release cycle; keeping them apart means a CASS migration and
-- an Oasis upgrade cannot collide in the same namespace.
--
-- Both are owned by the same role as the control-plane database. Section 10's
-- separation of duties is enforced at the API, not by database roles, and a
-- second credential to distribute would be a secret to leak without being a
-- boundary anyone checks.

-- The Oasis Platform's own Django database: portfolios, analyses, task status.
SELECT 'CREATE DATABASE oasis OWNER CURRENT_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'oasis')\gexec

-- The celery result backend shared by the Oasis API and its model workers.
SELECT 'CREATE DATABASE celery OWNER CURRENT_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'celery')\gexec
