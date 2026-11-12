# Stand-in for the web image, used only to test the deploy tool. It uses the
# real web server configuration, so the API forwarding is the real thing.
ARG BASE
FROM ${BASE}
ARG BUILD_SHA
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY proxy_params /etc/nginx/proxy_params
COPY html/ /usr/share/nginx/html/
# The same two settings as apps/web/Dockerfile (tests/test_deploy_tool.py
# checks they stay the same).
ENV NGINX_ENVSUBST_FILTER="^EDFINDER_(API_UPSTREAM|CLIENT_ADDRESS_FROM)$" \
    EDFINDER_CLIENT_ADDRESS_FROM="x_real_ip"
LABEL org.opencontainers.image.revision="$BUILD_SHA"
EXPOSE 8080
