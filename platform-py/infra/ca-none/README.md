# No corporate CA

Empty on purpose: mounted at /config/ca in the hub when install.sh has no --ca-file. With --ca-file the
install copies the CA to .secrets/ca/ca.pem and mounts that folder instead (HUB_CA_DIR).
