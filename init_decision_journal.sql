-- Run once in Supabase SQL Editor, using the same DB as the existing site.
-- Custom BIGINT users + server JWT login: no direct browser/anon/authenticated
-- access. All access goes through verified app sessions and the service key.
CREATE TABLE IF NOT EXISTS public.decision_journal (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    schema INTEGER NOT NULL DEFAULT 1 CHECK (schema = 1),
    version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    plan JSONB NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
    outcome JSONB NOT NULL CHECK (jsonb_typeof(outcome) = 'object'),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
);
CREATE INDEX IF NOT EXISTS decision_journal_owner_date
    ON public.decision_journal(user_id, created_at);
ALTER TABLE public.decision_journal ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.decision_journal FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.decision_journal TO service_role;
-- No public read policy; unlike public market artifacts, journals are private.
