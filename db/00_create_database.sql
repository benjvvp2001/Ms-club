-- Run this file once in pgAdmin's Query Tool, connected to the
-- "sportmatch_clubs" database as an administrator (postgres). Replace the
-- placeholder with DB_PASSWORD from ms-club/.env before executing it.
--
-- The tables (club, deporte, club_deporte, interesado_club) already exist and
-- belong to postgres. This only creates the service role and lets it read the
-- tables the clubs directory uses; it does not change any table or owner.

CREATE ROLE sportmatch_clubs
    LOGIN
    PASSWORD 'replace-with-DB_PASSWORD-from-.env'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT;

GRANT CONNECT ON DATABASE sportmatch_clubs TO sportmatch_clubs;
GRANT USAGE ON SCHEMA public TO sportmatch_clubs;
GRANT SELECT ON club, deporte, club_deporte TO sportmatch_clubs;
