FROM node:22-alpine AS build
WORKDIR /app
COPY apps/training-workspace/package*.json ./
RUN npm ci
COPY apps/training-workspace/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/training-workspace/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
USER 101
