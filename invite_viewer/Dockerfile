FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* vars are inlined into the client bundle at build time, so the
# API base URL must be known here, not just at container runtime.
ARG NEXT_PUBLIC_INVITE_API_BASE_URL
ENV NEXT_PUBLIC_INVITE_API_BASE_URL=${NEXT_PUBLIC_INVITE_API_BASE_URL}
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    PORT=8080 \
    HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

EXPOSE 8080
CMD ["node", "server.js"]
