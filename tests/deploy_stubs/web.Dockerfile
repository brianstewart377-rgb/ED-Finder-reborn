# Stand-in for the web image, used only to test the deploy tool. It uses the
# real web server configuration, so the API forwarding is the real thing.
ARG BASE
FROM ${BASE}
ARG BUILD_SHA
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY proxy_params /etc/nginx/proxy_params
COPY html/ /usr/share/nginx/html/
ENV NGINX_ENVSUBST_FILTER="^EDFINDER_API_UPSTREAM$"
LABEL org.opencontainers.image.revision="$BUILD_SHA"
EXPOSE 8080
