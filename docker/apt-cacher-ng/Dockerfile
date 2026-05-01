# Use Debian 13 (Trixie) slim image
FROM debian:13-slim

# Ensure user/group exists with predictable IDs (Debian default is 101:101)
# The apt-cacher-ng package usually creates this, but we enforce it for reproducibility
RUN id -u apt-cacher-ng &>/dev/null || \
    (groupadd -g 101 apt-cacher-ng && useradd -u 101 -g 101 -r -s /usr/sbin/nologin apt-cacher-ng)

# Install only essential packages: apt-cacher-ng + wget (for healthcheck)
# Use --no-install-recommends to minimize attack surface and image size
RUN apt-get update && \
    apt-get install -y --no-install-recommends apt-cacher-ng wget tini && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create runtime directories with correct ownership
# These will be mounted as volumes at runtime, but need initial permissions for read_only containers
RUN mkdir -p /var/cache/apt-cacher-ng /var/log/apt-cacher-ng /run/apt-cacher-ng && \
    chown -R 101:101 /var/cache/apt-cacher-ng /var/log/apt-cacher-ng /run/apt-cacher-ng

# Remove default configs that conflict with custom mounts
# Prevents package-generated defaults from overriding your hardened configuration
RUN rm -f /etc/apt-cacher-ng/acng.conf /etc/apt-cacher-ng/zz_debconf.conf

# Install the entrypoint wrapper that keeps the daemon running and triggers
# periodic cache maintenance inside the container.
COPY entrypoint.sh /usr/local/bin/apt-cacher-ng-entrypoint.sh
RUN chmod 0755 /usr/local/bin/apt-cacher-ng-entrypoint.sh

# Expose the default apt-cacher-ng proxy port
EXPOSE 3142

# Drop root privileges permanently. All runtime processes run as non-root.
USER 101:101

# 🪵 Symlink log files to stdout/stderr for Docker visibility
# This replaces the "tail -F" pattern without backgrounding or PID 1 hijacking
RUN ln -sf /dev/stdout /var/log/apt-cacher-ng/apt-cacher.log && \
    ln -sf /dev/stderr /var/log/apt-cacher-ng/apt-cacher.err && \
    ln -sf /dev/stdout /var/log/apt-cacher-ng/apt-cacher.dbg

# 📦 VOLUME DECLARATIONS
# /var/cache/apt-cacher-ng - Persistent storage for cached .deb/.rpm packages, index files, and metadata
# Docker will automatically provision anonymous volumes for these paths at runtime
# unless explicitly overridden with -v/--mount or bind mounts in docker-compose.
# Defining them here serves as documentation and ensures data persistence by default.
VOLUME ["/var/cache/apt-cacher-ng"]

# 🩺 HEALTHCHECK
# Verifies the built-in HTTP report page is accessible.
# --start-period: Grace period for cache/index initialization before health checks begin
# --interval: How often to run the check
# --timeout: Max time allowed per check
# --retries: Number of consecutive failures before marking unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q --spider http://localhost:3142/acng-report.html || exit 1

# Use tini as PID 1 for proper signal forwarding + zombie reaping.
# The wrapper starts apt-cacher-ng and runs maintenance on a timer.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/apt-cacher-ng-entrypoint.sh"]
CMD ["/usr/sbin/apt-cacher-ng", "-c", "/etc/apt-cacher-ng", "ForeGround=1", "UnbufferLogs=1"]
