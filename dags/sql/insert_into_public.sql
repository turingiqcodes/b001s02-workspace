-- Promote staged authors to public.authors.
-- Run by the insert_into_public task of b001s02_pipeline_dag.
-- Only au_ids not already in public are inserted, so re-runs are safe.
INSERT INTO public.authors (au_id, au_lname, au_fname, phone, address, city, state, zip, contract)
SELECT au_id, au_lname, au_fname, phone, address, city, state, zip, contract
FROM stage.authors
ON CONFLICT (au_id) DO NOTHING;
