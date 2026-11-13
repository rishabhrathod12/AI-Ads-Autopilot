"""Let a signed-in user see their own memberships and organizations; unique email ignoring case.

Before an organization is chosen there is no tenant, so "which organizations am I in?" cannot use
the tenant policy. A second, read-only policy admits rows that belong to the verified subject the
server put in ``app.subject`` (transaction-local, set only after token verification). Policies are
combined with OR, so the tenant policy still decides everything once a tenant is set, and with no
subject set (every ordinary request) the new policies match nothing.

Invited users are stored lower-cased and looked up with ``lower(email)``; the unique index stops
two rows differing only by case from ever being linked ambiguously.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE POLICY member_self_read ON memberships FOR SELECT USING (user_id IN "
        "(SELECT id FROM users "
        "WHERE auth_subject = nullif(current_setting('app.subject', true), '')))"
    )
    op.execute(
        "CREATE POLICY member_org_read ON organizations FOR SELECT "
        "USING (id IN (SELECT organization_id FROM memberships))"
    )
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
    op.execute("DROP POLICY IF EXISTS member_org_read ON organizations")
    op.execute("DROP POLICY IF EXISTS member_self_read ON memberships")
