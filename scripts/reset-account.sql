-- reset-account.sql
--
-- Wipes ALL workspace data for the user identified by :email and
-- upgrades any remaining workspace they own to plan_tier='frontier'.
--
-- Use case: founder's E2E demo — start from a clean slate (no
-- workspaces, no projects, no clusters, no feedback, no orchestrator
-- runs) so the Onboarding Wizard fires correctly, then re-stamp the
-- workspace tier so Frontier-only Claude code-gen unlocks.
--
-- Run with:
--   psql "$DATABASE_URL_PROD" \
--     -v email="'founder@example.com'" \
--     -f scripts/reset-account.sql
--
-- The script is wrapped in a single transaction. If any statement
-- fails, NOTHING is committed.
--
-- After running:
--   1. Sign in.
--   2. The Onboarding Wizard should fire (user has no workspaces).
--   3. Complete the wizard — a fresh workspace is created.
--   4. Run scripts/upgrade-workspace-frontier.sql with that
--      workspace's id (or just run reset-account.sql again now that
--      the workspace exists; the SET below will catch it).
--
-- Safety:
--   - Targets ONE user only (the one matching :email).
--   - Does NOT delete the users row itself — just owned data.
--   - Other users' data is untouched (every DELETE is scoped by
--     workspace_id IN (user's workspaces) or user_id = the user).

\set ON_ERROR_STOP on

BEGIN;

-- Resolve the user_id once and stash it in a temp table so every
-- subsequent statement can reference it without recomputing.
CREATE TEMP TABLE _target_user ON COMMIT DROP AS
SELECT user_id, email
FROM users
WHERE email = :email;

DO $$
DECLARE
    n_users INT;
    target_email TEXT;
BEGIN
    SELECT COUNT(*), MAX(email) INTO n_users, target_email FROM _target_user;
    -- Note: psql colon-prefixed variable substitution does NOT
    -- occur inside dollar-quoted PL/pgSQL bodies. Pull the email
    -- from the temp table instead. (Founder hit the syntax error
    -- 2026-05-04.)
    IF n_users = 0 THEN
        RAISE EXCEPTION 'No user found with that email';
    ELSIF n_users > 1 THEN
        RAISE EXCEPTION 'Ambiguous: multiple users match email = %', target_email;
    END IF;
END $$;

-- Stash the user's workspace ids so we can scope every DELETE.
CREATE TEMP TABLE _target_workspaces ON COMMIT DROP AS
SELECT DISTINCT w.workspace_id
FROM workspaces w
LEFT JOIN workspace_members m ON m.workspace_id = w.workspace_id
WHERE w.billing_owner_user_id IN (SELECT user_id FROM _target_user)
   OR m.user_id IN (SELECT user_id FROM _target_user);

-- Stash the v2 project ids so per-project child tables can be
-- cleaned out by project_id (workspace_id alone doesn't scope them).
CREATE TEMP TABLE _target_v2_projects ON COMMIT DROP AS
SELECT project_id
FROM v2_projects
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces)
   OR user_id IN (SELECT user_id FROM _target_user);

-- Stash legacy v1 project ids too. The v1 ``projects`` table uses
-- ``owner`` for the user pointer, not ``user_id`` — column name
-- differs from the v2 schema.
CREATE TEMP TABLE _target_v1_projects ON COMMIT DROP AS
SELECT project_id
FROM projects
WHERE owner IN (SELECT user_id FROM _target_user);

\echo '--- Target user / workspaces / projects ---'
SELECT * FROM _target_user;
SELECT * FROM _target_workspaces;
SELECT 'v2_projects' AS kind, COUNT(*) FROM _target_v2_projects
UNION ALL
SELECT 'v1_projects' AS kind, COUNT(*) FROM _target_v1_projects;

-- =====================================================================
-- 1. Per-project child tables (FK to v2_projects.project_id OR
--    projects.project_id). Order: leaf tables first.
-- =====================================================================

-- v2 / W2-W3 surfaces
DELETE FROM decision_provenance
WHERE decision_id IN (
    SELECT decision_id FROM decisions
    WHERE project_id IN (SELECT project_id FROM _target_v2_projects)
       OR project_id IN (SELECT project_id FROM _target_v1_projects)
);
DELETE FROM decision_versions
WHERE decision_id IN (
    SELECT decision_id FROM decisions
    WHERE project_id IN (SELECT project_id FROM _target_v2_projects)
       OR project_id IN (SELECT project_id FROM _target_v1_projects)
);
DELETE FROM cascade_runs
WHERE project_id IN (SELECT project_id FROM _target_v2_projects)
   OR project_id IN (SELECT project_id FROM _target_v1_projects);
DELETE FROM decisions
WHERE project_id IN (SELECT project_id FROM _target_v2_projects)
   OR project_id IN (SELECT project_id FROM _target_v1_projects);
DELETE FROM relationships
WHERE project_id IN (SELECT project_id FROM _target_v2_projects)
   OR project_id IN (SELECT project_id FROM _target_v1_projects);
-- qna_turns has a FK to topics(topic_id), so it MUST be deleted
-- before topics. Previously this DELETE lived below — moved up so
-- the script doesn't trip ForeignKeyViolation on workspaces with
-- any Q&A history.
DELETE FROM qna_turns
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM topics
WHERE project_id IN (SELECT project_id FROM _target_v2_projects)
   OR project_id IN (SELECT project_id FROM _target_v1_projects);
DELETE FROM open_questions
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM risks_assumptions
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM consistency_flags
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM context_sources
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM source_references
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM summary_versions
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM approval_actions
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM audit_log
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM artifacts
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM sessions
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM scaffolds
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM next_steps_artifacts
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM business_plan_phases
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM documents
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM shared_links
WHERE project_id IN (SELECT project_id FROM _target_v1_projects)
   OR project_id IN (SELECT project_id FROM _target_v2_projects);

-- =====================================================================
-- 2. Project rows themselves
-- =====================================================================

DELETE FROM v2_projects
WHERE project_id IN (SELECT project_id FROM _target_v2_projects);
DELETE FROM projects
WHERE project_id IN (SELECT project_id FROM _target_v1_projects);

-- =====================================================================
-- 3. Workspace-scoped W3 pipeline state
-- =====================================================================

-- Sub-agents reference orchestrator_runs by FK; clear the children
-- first.
DELETE FROM sub_agent_runs
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
-- conflict_resolutions has no workspace_id column — scope via the
-- orchestrator_runs FK instead (founder fix 2026-05-04).
DELETE FROM conflict_resolutions
WHERE orchestrator_run_id IN (
    SELECT run_id FROM orchestrator_runs
    WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces)
);
DELETE FROM orchestrator_runs
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
DELETE FROM prioritization_runs
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
DELETE FROM feedback_clusters
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
DELETE FROM feedback_items
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
DELETE FROM connector_sync_runs
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
DELETE FROM repo_snapshots
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);
DELETE FROM connector_credentials
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);

-- =====================================================================
-- 4. Workspace shells + memberships — DELETE so Onboarding Wizard
--    fires for the user on next login.
-- =====================================================================

DELETE FROM workspace_members
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces)
   OR user_id IN (SELECT user_id FROM _target_user);
DELETE FROM workspaces
WHERE workspace_id IN (SELECT workspace_id FROM _target_workspaces);

-- =====================================================================
-- 5. User-scoped non-workspace state (shelves, credits, usage)
-- =====================================================================

DELETE FROM shelves WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM user_credits WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM credit_transactions WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM user_usage WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM tier_usage WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM business_plan_usage WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM subscriptions WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM user_access_tokens WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM password_reset_tokens WHERE user_id IN (SELECT user_id FROM _target_user);
DELETE FROM suggestions_cache WHERE user_id IN (SELECT user_id FROM _target_user);

-- Clear the cached default workspace pointer so /api/auth/me returns
-- default_workspace_id=NULL → RootGate dispatches to /onboarding.
UPDATE users
SET default_workspace_id = NULL
WHERE user_id IN (SELECT user_id FROM _target_user);

-- =====================================================================
-- 6. (post-onboarding follow-up) Upgrade any workspace the user
--    currently owns to plan_tier='frontier'. Safe to run before they
--    have a workspace — the UPDATE just hits 0 rows. After they go
--    through the Onboarding Wizard, re-run this script and the
--    UPDATE here will stamp Frontier on the freshly-minted workspace.
-- =====================================================================

UPDATE workspaces
SET plan_tier = 'frontier'
WHERE billing_owner_user_id IN (SELECT user_id FROM _target_user);

\echo '--- After-state: any workspaces left for this user ---'
SELECT workspace_id, name, slug, plan_tier
FROM workspaces
WHERE billing_owner_user_id IN (SELECT user_id FROM _target_user);

COMMIT;

\echo '✓ Reset complete. Sign out + sign back in to land on the Onboarding Wizard.'
\echo '  After completing onboarding, re-run this script to upgrade the new workspace to plan_tier=frontier.'
