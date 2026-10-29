-- Usernames are case-insensitive: "MGreen" and "mgreen" are the same login.
-- The backend saves them lower-cased and compares with lower(username) at sign-in.
--
-- If two existing usernames differ only by case, the index can't be built and the
-- whole migration rolls back; rename one of them first.
--
-- Safe to run more than once.

BEGIN;

UPDATE public.app_user SET username = lower(username) WHERE username <> lower(username);

CREATE UNIQUE INDEX IF NOT EXISTS app_user_username_lower_idx ON public.app_user (lower(username));

COMMIT;
