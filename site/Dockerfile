FROM node:22-slim

WORKDIR /workspace/site
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ENV PUBLISH_BUNDLE_DIR=/workspace/publish-bundle
CMD ["pnpm", "build"]
