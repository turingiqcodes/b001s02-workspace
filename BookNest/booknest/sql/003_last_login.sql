-- Remember each account's last sign-in on the account itself, so it survives
-- `python -m app.manage prune-keys` deleting old API keys.
--
-- Safe to run more than once.

BEGIN;

ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

-- Fill it once from the keys that are still there.
UPDATE public.app_user u SET last_login_at = k.at
FROM (SELECT user_id, MAX(created_at) AS at FROM public.api_key GROUP BY user_id) k
WHERE k.user_id = u.user_id AND u.last_login_at IS NULL;

COMMIT;
