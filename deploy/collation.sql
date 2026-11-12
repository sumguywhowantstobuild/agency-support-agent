-- After the database's base system changes (a new glibc, as in the move from Debian bookworm to trixie), text may
-- sort differently, so an index built under the old rules can miss rows or let a duplicate through. deploy/apply.sh
-- runs this in each database whose recorded sorting-rules version differs from the system's (`copy images`,
-- `live images`): rebuild every index, then record the new version. REINDEX can't run inside a transaction, so
-- this runs statement by statement and stops at the first error. DBNAME is psql's own: the database it is in.
\set ON_ERROR_STOP 1
\set QUIET 1
SET client_min_messages = warning; -- one "changing version" note per named collation would be hundreds of lines
\echo == :DBNAME: rebuilding every index under the new sorting rules
REINDEX DATABASE :"DBNAME";
ALTER DATABASE :"DBNAME" REFRESH COLLATION VERSION;
-- Named collations carry their own version too (initdb copies every system locale in); only matters for an index
-- that names one, and those were rebuilt above. The database's default (provider 'd') is the line just above.
SELECT count(*) AS stale FROM pg_collation
 WHERE collprovider <> 'd' AND collversion IS DISTINCT FROM pg_collation_actual_version(oid) \gset
\echo == :DBNAME: :stale named collations get the new version
SELECT format('ALTER COLLATION %s REFRESH VERSION', oid::regcollation) FROM pg_collation
 WHERE collprovider <> 'd' AND collversion IS DISTINCT FROM pg_collation_actual_version(oid) \gexec
\echo == :DBNAME: done
