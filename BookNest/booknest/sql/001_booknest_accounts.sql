-- BookNest accounts and API keys.
-- Adds two tables to the public schema, next to the pubs tables. Nothing in the
-- existing pubs tables (public.authors, public.titles, ...) is altered or deleted.
--
--   public.app_user  one row per login: the admin (publisher) or an author
--   public.api_key   keys handed out at login; only a SHA-256 hash is stored
--
-- Safe to run more than once.

BEGIN;

CREATE TABLE IF NOT EXISTS public.app_user (
    user_id       serial PRIMARY KEY,
    username      text        NOT NULL UNIQUE,
    display_name  text        NOT NULL,
    email         text,
    password_hash text        NOT NULL,           -- scrypt hash made by the backend, never the password
    role          text        NOT NULL CHECK (role IN ('admin', 'author')),
    -- The pubs author this login belongs to. Not a foreign key, because the
    -- pubs load does not guarantee a unique key on public.authors(au_id);
    -- the backend checks that the author exists.
    au_id         varchar(11) UNIQUE,
    is_active     boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT author_has_au_id CHECK ((role = 'author') = (au_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS public.api_key (
    key_id       serial PRIMARY KEY,
    user_id      integer     NOT NULL REFERENCES public.app_user (user_id) ON DELETE CASCADE,
    key_hash     char(64)    NOT NULL UNIQUE,    -- hex SHA-256 of the key; the key itself is never stored
    key_prefix   text        NOT NULL,           -- first characters, to recognise a key in lists
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    last_used_at timestamptz,
    revoked_at   timestamptz
);

CREATE INDEX IF NOT EXISTS api_key_user_id_idx ON public.api_key (user_id);

COMMIT;
