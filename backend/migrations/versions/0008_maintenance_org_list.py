"""Let the scheduled-job runner list organizations (and nothing else) across tenants.

Scheduled jobs (``python -m app.cli run-jobs``) must know which organizations exist, but row-level
security hides every organization when no tenant is set. This adds one read-only policy on
``organizations`` that admits rows while the transaction-local setting ``app.maintenance`` is
``on``. Only the job runner sets it, in a transaction that does nothing but read ids; every job
then runs per organization in an ordinary tenant session. With the setting absent (every request)
the policy matches nothing. The trust model is the same as ``app.org_id`` and ``app.subject``: the
server sets these settings, never a request field.

A SECURITY DEFINER function was rejected: ``organizations`` has FORCE ROW LEVEL SECURITY, which
applies to the function's owner too unless that role has BYPASSRLS, so it would return nothing on
a managed database whose owner is an ordinary role.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE POLICY maintenance_list_orgs ON organizations FOR SELECT "
        "USING (current_setting('app.maintenance', true) = 'on')"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS maintenance_list_orgs ON organizations")
