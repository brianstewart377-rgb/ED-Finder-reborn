# syntax=docker/dockerfile:1
FROM ghcr.io/brianstewart377-rgb/ed-finder/mirror/node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS build

ARG BUILD_SHA
ARG VITE_FINDER_F4_ENABLED=0
WORKDIR /workspace/apps/web

# Cypress is a development dependency for the protected browser lane, but its
# downloaded browser binary is not part of the static release image. Keep the
# frozen install deterministic without fetching that unrelated binary.
ENV CYPRESS_INSTALL_BINARY=0

RUN case "$BUILD_SHA" in \
      *[!0-9a-f]*|'') exit 64 ;; \
    esac && [ "${#BUILD_SHA}" -eq 40 ]

RUN case "$VITE_FINDER_F4_ENABLED" in \
      0|1) ;; \
      *) exit 64 ;; \
    esac

RUN corepack enable && corepack prepare pnpm@11.25.0 --activate
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
COPY apps/importer/src/data/region_map.json /workspace/apps/importer/src/data/region_map.json
COPY assets/PROVENANCE.json /workspace/assets/PROVENANCE.json
# The frozen graph contains a file: dependency on api-client, so its complete
# package must exist at the repository-relative path while pnpm installs it.
# shared:build also compiles both packages, in api-client -> planner-core order.
COPY packages/api-client/ /workspace/packages/api-client/
COPY packages/planner-core/ /workspace/packages/planner-core/
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm run shared:build
RUN VITE_BUILD_SHA="$BUILD_SHA" VITE_FINDER_F4_ENABLED="$VITE_FINDER_F4_ENABLED" pnpm build
# Bind the HTML itself to the image source, including the SPA fallback.
RUN BUILD_SHA="$BUILD_SHA" node -e 'const fs=require("node:fs"); for (const file of ["build/index.html","build/200.html"]) { const html=fs.readFileSync(file,"utf8"); if (!html.includes("</head>") || html.includes("edfinder-build-sha")) process.exit(64); fs.writeFileSync(file,html.replace("</head>",`<meta name="edfinder-build-sha" content="${process.env.BUILD_SHA}"></head>`)); }'

FROM ghcr.io/brianstewart377-rgb/ed-finder/mirror/nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de AS runtime

ARG BUILD_SHA
RUN case "$BUILD_SHA" in \
      *[!0-9a-f]*|'') exit 64 ;; \
    esac && [ "${#BUILD_SHA}" -eq 40 ]

COPY apps/web/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY apps/web/nginx/proxy_params /etc/nginx/proxy_params
COPY --from=build /workspace/apps/web/build/ /usr/share/nginx/html/

# Only substitute the reviewed API upstream. nginx variables such as $host,
# $uri and $request_id must survive template rendering unchanged.
ENV NGINX_ENVSUBST_FILTER="^EDFINDER_API_UPSTREAM$" \
    BUILD_SHA="$BUILD_SHA"
LABEL org.opencontainers.image.revision="$BUILD_SHA"

EXPOSE 8080
