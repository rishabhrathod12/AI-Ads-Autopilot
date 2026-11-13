# ADR 0026: Auth0 sign-in with invitation-only access

Status: Accepted by the owner on 2026-11-13 (provider: Auth0; provisioning: admin invites by email). Backend and frontend
implemented; **not yet exercised against a real Auth0 tenant**.

## Context
ADR 0021 made the API verify provider-issued JWTs but left three things open: which provider, how a person gets a membership,
and how the frontend signs in. Without them the shipped frontend (development headers only) cannot talk to a production API.

## Decision
- **Provider: Auth0.** The API still only verifies tokens (ADR 0021); nothing in the backend is Auth0-specific except the
  documented claim names. The frontend uses the official `@auth0/auth0-react` SDK (authorization code flow with PKCE,
  hosted login page, access token for our API audience). No passwords or secrets are handled by AdPilot; the client id,
  domain and audience in `VITE_AUTH0_*` are public identifiers.
- **Invitation only.** There is no self-signup and nobody can create an organization from the UI.
  - An admin or owner posts an email and a role to `POST /api/v1/members`. A `users` row (lower-cased email, no subject yet)
    and a `memberships` row are created. Nobody can grant a role above their own.
  - The first owner of an organization is created with `python -m app.cli bootstrap-owner --email ...`.
  - **First login links the invitation.** If a verified token's subject has no membership in the chosen organization, and the
    token carries an email the provider marks verified, the server sets `users.auth_subject` on the still-unlinked invited
    row with that email. Rules: only a literal JSON `true` counts as verified; a row that already has a subject is never
    re-linked; one subject can only ever link one row; the role always comes from the membership row, never from a token.
- **`GET /api/v1/me`** lists the organizations the verified subject belongs to (needs a token, no organization), so the
  frontend can pick one instead of asking for a UUID. Not available when auth is not configured (404).
- **Database (migration 0007).** Before an organization is chosen there is no tenant, so two read-only RLS policies
  (`memberships.member_self_read`, `organizations.member_org_read`) admit rows that belong to the subject the server placed
  in the transaction-local setting `app.subject` after verification. With no subject set (every ordinary request) they match
  nothing, and the tenant policy still decides everything once a tenant is set. A unique index on `lower(users.email)`
  prevents two rows that differ only by case.
- **Audit.** `member.invited` is written with the membership id and role; the email address is not stored (the audit
  scrubber would replace it anyway). Linking is logged without the address.

## Auth0 specifics that are easy to get wrong
- Auth0 **access tokens do not contain `email` or `email_verified`** by default. Add both with a post-login Action as
  *namespaced* claims (Auth0 requires a URL-style namespace for custom claims) and set `AUTH_EMAIL_CLAIM` and
  `AUTH_EMAIL_VERIFIED_CLAIM` to those names. See `docs/AUTH_SETUP.md`. Without them nobody can be linked and every
  invited user gets 403.
- Use an Auth0 *Database* or social connection that **verifies email**; unverified emails are ignored by design.
- The SPA keeps tokens in memory (SDK default). A page reload obtains a new token silently from the Auth0 session; browsers
  that block third-party cookies may need Auth0 refresh-token rotation (not enabled here).

## Consequences
- The backend change needs no new dependency; the frontend adds `@auth0/auth0-react` (bundle 249 kB to 491 kB, 78 kB to 145 kB gzipped). Its
  `browser-tabs-lock` dependency has an install script: review it when updating.
- **Not done / open:** an Auth0 tenant, application and API do not exist yet; the Action is untested; removing or changing a
  member's role, resending or revoking an invitation, listing pending invitations across organizations, rate limiting of
  failed sign-ins, token revocation beyond expiry, and email delivery (the admin tells the person; no email is sent).
- **Container deployment:** `frontend/nginx.conf` sets `connect-src 'self'`, which blocks the SDK's calls to the Auth0 domain,
  and the frontend `Dockerfile` has no build arguments for `VITE_AUTH0_*`. Both must change, and be built and tested with
  Docker, before the container can sign anyone in. Not changed here because Docker is not available to verify it.
- An invited user who is also invited to a second organization is the same `users` row; one login covers both organizations.
- Email takeover relies on the provider's email verification. If a provider ever marks unverified emails as verified, an
  attacker who registers an invited address could claim the invitation.
