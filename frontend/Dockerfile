# The development frontend: Vite's dev server with hot reload, the source
# bind-mounted over /app by docker-compose.yml. The published image builds the
# frontend in the root Dockerfile instead.
FROM node:24-alpine

WORKDIR /app

# From the lockfile, so the container gets the versions that were tested
# rather than whatever the ranges in package.json resolve to today.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
