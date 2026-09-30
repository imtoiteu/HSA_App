# Production web image (canonical, self-contained): build the SPA with Node, serve it with nginx.
# On hosts with a slow registry connection use Dockerfile.prebuilt (see deploy/deploy.sh).
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
# rendered to conf.d/hsa.conf at start by the image's envsubst entrypoint (NGINX_LISTEN)
COPY nginx.conf /etc/nginx/templates/hsa.conf.template
COPY proxy_params_hsa /etc/nginx/proxy_params_hsa
COPY --from=build /web/dist /usr/share/nginx/html
# pre-compress text assets for gzip_static
RUN find /usr/share/nginx/html -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.svg' -o -name '*.json' \) -exec gzip -k -9 {} \;
ENV NGINX_LISTEN=80 NGINX_ENVSUBST_FILTER=NGINX_
EXPOSE 80
