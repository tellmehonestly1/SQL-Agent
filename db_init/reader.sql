-- Run as the DB owner after seed.sql; idempotent on existing volumes.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sqlagent_reader') THEN
    CREATE ROLE sqlagent_reader LOGIN PASSWORD 'sqlagent_reader';
  END IF;
END $$;
ALTER ROLE sqlagent_reader SET default_transaction_read_only = on;
DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO sqlagent_reader', current_database()); END $$;
GRANT USAGE ON SCHEMA public TO sqlagent_reader;
GRANT SELECT ON public.users, public.orders TO sqlagent_reader;
