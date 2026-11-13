# Auth0 setup (ADR 0026)

One-time setup to let people sign in to AdPilot. Everything below is done in the Auth0 dashboard and your `.env` files;
nothing here needs a secret in the frontend.

## 1. Create the API
1. Auth0 dashboard > Applications > APIs > Create API.
2. Identifier (this is the **audience**): for example `https://api.adpilot.example`. Signing algorithm: **RS256**.
3. Note your tenant domain, for example `your-tenant.eu.auth0.com`.

## 2. Create the application
1. Applications > Create Application > **Single Page Web Application**.
2. Allowed Callback URLs, Allowed Logout URLs and Allowed Web Origins: `http://localhost:5173` for development, your real
   origin for production.
3. Copy the **Client ID** (not a secret).
4. Authentication > Database: turn **off** "Disable Sign Ups" only if you want people to be able to create Auth0 logins;
   AdPilot still refuses anyone who has not been invited. Make sure email verification is on for the connection you use.

## 3. Add the email claims to the access token
Auth0 access tokens do not carry the user's email. Actions > Library > Build Custom > trigger **Login / Post Login**:

```js
exports.onExecutePostLogin = async (event, api) => {
  const ns = "https://adpilot.example"; // any URL you own; it is only a namespace
  api.accessToken.setCustomClaim(`${ns}/email`, event.user.email);
  api.accessToken.setCustomClaim(`${ns}/email_verified`, event.user.email_verified === true);
};
```
Deploy it, then Actions > Flows > Login: drag the Action into the flow and Apply.

## 4. Configure the backend (`backend/.env`)
```
AUTH_JWT_ISSUER=https://your-tenant.eu.auth0.com/        # note the trailing slash
AUTH_JWT_AUDIENCE=https://api.adpilot.example
AUTH_JWKS_URL=https://your-tenant.eu.auth0.com/.well-known/jwks.json
AUTH_EMAIL_CLAIM=https://adpilot.example/email
AUTH_EMAIL_VERIFIED_CLAIM=https://adpilot.example/email_verified
```
All three of the first group must be set together (the API refuses to start otherwise). With them set, the development
headers stop working.

## 5. Configure the frontend (`frontend/.env.local`)
```
VITE_AUTH0_DOMAIN=your-tenant.eu.auth0.com
VITE_AUTH0_CLIENT_ID=<the client id from step 2>
VITE_AUTH0_AUDIENCE=https://api.adpilot.example
```
If any of the three is missing the frontend falls back to the development flow.

## 6. Create the first owner, then invite others
```
cd backend
.venv/Scripts/python -m app.cli bootstrap-owner --email you@example.com --org-name "My Company"
```
It prints the organization id. Sign in with that email; the first login links you. After that, owners and admins invite
people from the **Team** panel (or `POST /api/v1/members`). Tell the person to sign in with the invited address.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Signed in but "No access yet" | the email was not invited, the token has no email claim (step 3), or `AUTH_EMAIL_*` names do not match the Action's claim names |
| 401 on every call | issuer without the trailing slash, wrong audience, or the frontend is not requesting the API audience |
| 403 "Not a member" | invited to a different organization, or the invitation was already linked to another login |
| Login works locally but not in the Docker image | nginx CSP `connect-src` and the missing `VITE_AUTH0_*` build arguments (see ADR 0026) |
