FROM alexgshaw/configure-git-webserver:20251031
RUN find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -exec sed -i 's|http://|https://|g' {} + \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends poppler-utils ripgrep python3
WORKDIR /app
