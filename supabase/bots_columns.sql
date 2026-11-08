-- Adds the columns the bot files need to an existing database. schema.sql
-- already has them, so a new project doesn't need this. Safe to re-run.

begin;

-- Both are shown to teams, so they must never hold a password or hidden prompt.
alter table bots add column if not exists description text not null default '';
alter table bots add column if not exists goal text not null default '';

-- The provider's real model ID, and the service that runs the model. Teams never
-- see these.
alter table models add column if not exists api_model text;
alter table models add column if not exists host text;

commit;
