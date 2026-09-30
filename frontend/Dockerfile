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
COPY nginx.conf /etc/nginx/conf.d/hsa.conf
COPY proxy_params_hsa /etc/nginx/proxy_params_hsa
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
