# Frontend image. UNVERIFIED: written without Docker available; build it before relying on it.
# Node matches .nvmrc (the same major as CI). Base-image tags are not pinned by digest.
FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Public sign-in identifiers (never secrets), baked into the bundle by Vite. All three empty means
# the development flow (typed organization id), which the API refuses in production.
ARG VITE_AUTH0_DOMAIN=
ARG VITE_AUTH0_CLIENT_ID=
ARG VITE_AUTH0_AUDIENCE=
ENV VITE_AUTH0_DOMAIN=$VITE_AUTH0_DOMAIN \
    VITE_AUTH0_CLIENT_ID=$VITE_AUTH0_CLIENT_ID \
    VITE_AUTH0_AUDIENCE=$VITE_AUTH0_AUDIENCE
RUN npm run build

# Unprivileged nginx: runs as a non-root user and listens on 8080.
FROM nginxinc/nginx-unprivileged:stable-alpine
ARG VITE_AUTH0_DOMAIN=
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Allow the browser to reach the Auth0 domain (CSP connect-src and frame-src). The value goes into a
# config file, so it must be a plain hostname: anything else fails the build instead of being injected.
RUN case "$VITE_AUTH0_DOMAIN" in \
      "") origin="" ;; \
      *[!A-Za-z0-9.-]*) echo "VITE_AUTH0_DOMAIN must be a plain hostname" >&2; exit 1 ;; \
      *) origin="https://$VITE_AUTH0_DOMAIN" ;; \
    esac \
 && sed -i "s|__AUTH0_ORIGIN__|$origin|g" /etc/nginx/conf.d/default.conf \
 && ! grep -q "__AUTH0_ORIGIN__" /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=4s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
