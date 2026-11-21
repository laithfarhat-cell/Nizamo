# Stage 1: Build optimized React + TypeScript bundle
FROM node:20-alpine AS builder
WORKDIR /app/frontend
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Lightweight production Nginx serving static assets + /api reverse proxy
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/frontend/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
