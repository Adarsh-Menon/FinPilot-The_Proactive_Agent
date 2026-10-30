-- Apply as schema owner after SQLAlchemy creates tables. Roles are provisioned separately.
-- The app must connect as finpilot_app, never the table owner or postgres superuser.
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['profiles', 'transactions', 'imports', 'runs', 'goals', 'tenant_keys', 'rewards', 'debts', 'preferences'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))', table_name);
  END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO finpilot_app, finpilot_ops;
GRANT SELECT, INSERT, UPDATE, DELETE ON users, sessions, profiles, transactions, imports, runs, goals, tenant_keys, rewards, referral_codes, debts, preferences TO finpilot_app;
GRANT SELECT ON policy TO finpilot_app;
GRANT INSERT ON metrics, activity TO finpilot_app;
GRANT SELECT, INSERT, UPDATE ON feedback TO finpilot_app;
GRANT SELECT ON metrics, policy, policy_audit, activity, feedback TO finpilot_ops;
GRANT UPDATE ON policy TO finpilot_ops;
GRANT INSERT ON policy_audit TO finpilot_ops;
-- The daily AI-request limit reads who did what and when, never other activity columns.
GRANT SELECT (actor_ref, action, created_at) ON activity TO finpilot_app;
-- Alerts and AI spend are written where errors and model calls happen; budgets and suspensions are read there.
GRANT SELECT, INSERT, UPDATE ON alerts, ai_spend TO finpilot_app;
GRANT SELECT ON ops_settings, suspensions TO finpilot_app;
GRANT SELECT, UPDATE ON alerts TO finpilot_ops;
GRANT SELECT ON ai_spend TO finpilot_ops;
GRANT SELECT, INSERT, UPDATE ON ops_settings TO finpilot_ops;
GRANT SELECT, INSERT, DELETE ON suspensions TO finpilot_ops;
GRANT SELECT, INSERT ON admin_audit TO finpilot_ops;
-- Shared examples for goldens: written and withdrawn by the app, reviewed and pruned by operations.
GRANT SELECT, INSERT, DELETE ON golden_contributions TO finpilot_app;
GRANT SELECT, DELETE ON golden_contributions TO finpilot_ops;
-- Realized forecast accuracy: content-free scores written when a later forecast covers an earlier one's period.
GRANT SELECT, INSERT ON forecast_accuracy TO finpilot_app;
GRANT SELECT ON forecast_accuracy TO finpilot_ops;
REVOKE ALL ON users, sessions, profiles, transactions, imports, runs, goals, tenant_keys, rewards, referral_codes, debts, preferences FROM finpilot_ops;
