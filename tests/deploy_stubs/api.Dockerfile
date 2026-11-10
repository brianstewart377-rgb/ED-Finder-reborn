# Stand-in for the API image, used only to test the deploy tool.
ARG BASE
FROM ${BASE}
ARG BUILD_SHA
ARG DATABASE=connected
ARG FIND_ANSWER='{"results":[{"name":"Stand-in","claim_status":"free"}]}'
COPY api.conf.template /etc/nginx/templates/default.conf.template
ENV STUB_BUILD_SHA="$BUILD_SHA" \
    STUB_DATABASE="$DATABASE" \
    STUB_FIND_ANSWER="$FIND_ANSWER" \
    NGINX_ENVSUBST_FILTER="^STUB_"
LABEL org.opencontainers.image.revision="$BUILD_SHA"
EXPOSE 8000
