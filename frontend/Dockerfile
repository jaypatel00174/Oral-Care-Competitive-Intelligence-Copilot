# Web app image: build the React app, then serve it with nginx, which also forwards /api/* to the backend.
#
#   docker build -t oralcare-web frontend
#   docker run --rm -p 127.0.0.1:8080:8080 -e API_UPSTREAM=http://host.docker.internal:8000 oralcare-web
#
# API_UPSTREAM is where the backend API is reachable from this container (default: the compose service "api").

FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
# nginx's image fills ${...} in /etc/nginx/templates/*.template from the environment at start.
ENV API_UPSTREAM=http://api:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /web/dist /usr/share/nginx/html

# Run nginx without root: listen on 8080, and give the nginx user the folders it writes to.
RUN sed -i 's/^user .*;//' /etc/nginx/nginx.conf \
 && sed -i 's#pid .*;#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
 && chown -R nginx:nginx /var/cache/nginx /etc/nginx/conf.d /usr/share/nginx/html
USER nginx

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
