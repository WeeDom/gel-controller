# GEL Controller SSH Tunnel MVP

This setup allows many field controllers to be managed from one admin interface by using reverse SSH tunnels.

## Topology

- Controller runs local control API on `127.0.0.1:8765`.
- Controller opens reverse tunnel to `ssh.guard-e-loo.co.uk`.
- Bastion exposes each controller at a unique loopback port (example: `127.0.0.1:20001`).
- Flask admin calls those bastion endpoints.

## Controller Setup

1. Install dependencies:

```bash
sudo apt-get update
sudo apt-get install -y openssh-client autossh
```

2. Install launcher script + unit:

```bash
sudo mkdir -p /opt/gel-controller/bin
sudo install -m 0755 bin/gel-reverse-tunnel.sh /opt/gel-controller/bin/gel-reverse-tunnel.sh
sudo install -m 0644 bin/gel-reverse-tunnel.service /etc/systemd/system/gel-reverse-tunnel.service
```

3. Create environment file:

```bash
sudo mkdir -p /etc/gel-controller/ssh
sudo tee /etc/gel-controller/tunnel.env >/dev/null <<'EOF'
SSH_BASTION_HOST=ssh.guard-e-loo.co.uk
SSH_BASTION_USER=gel-user-controller-1
SSH_KEY_PATH=/etc/gel-controller/ssh/id_ed25519
REMOTE_BIND_ADDRESS=127.0.0.1
REMOTE_PORT=20001
LOCAL_TARGET_HOST=127.0.0.1
LOCAL_TARGET_PORT=8765
EOF
```

4. Enable service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gel-reverse-tunnel.service
sudo systemctl status gel-reverse-tunnel.service
```

## Bastion SSH Hardening

Use one Linux user per controller (for example `gel-controller-1`).

In each controller's `authorized_keys`, restrict key capabilities:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:20001" ssh-ed25519 AAAA... controller-1
```

Recommended `sshd_config` defaults:

```text
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AllowTcpForwarding remote
GatewayPorts no
X11Forwarding no
```

Reload SSH daemon after updates.

## Health Check

On bastion host:

```bash
curl -sS -X POST http://127.0.0.1:20001/capture-baseline -H 'Content-Type: application/json' -d '{}'
```

Expected result is JSON from the controller API.

## Admin Mapping

In Flask environment, map `controller_id` to reachable endpoint:

```dotenv
GEL_CONTROLLER_ENDPOINTS={"gel-controller-1":"http://127.0.0.1:20001"}
```

If Flask is on another host, point to bastion private IP instead of `127.0.0.1`.

## Dockerized Flask On Bastion (Connection Refused Fix)

When Flask runs in a Docker container on the same bastion host, reverse tunnel ports bound to `127.0.0.1` are not reachable from the container.

- Host check works:

```bash
curl -sS http://127.0.0.1:20001/status
```

- Container check fails (before fix):

```bash
docker compose exec web curl -sS http://host.docker.internal:20001/status
```

Create a bridge service on the host:

```bash
sudo tee /etc/systemd/system/gel-controller-bridge-20001.service >/dev/null <<'EOF'
[Unit]
Description=Bridge Docker traffic to GEL controller tunnel 20001
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/socat TCP-LISTEN:20001,bind=172.17.0.1,reuseaddr,fork TCP:127.0.0.1:20001
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now gel-controller-bridge-20001.service
sudo systemctl status gel-controller-bridge-20001.service --no-pager
```

For Dockerized Flask, set endpoint mapping to host gateway:

```dotenv
GEL_CONTROLLER_ENDPOINTS={"gel-controller-1":"http://host.docker.internal:20001"}
```

Verify from container:

```bash
docker compose exec web curl -sS http://host.docker.internal:20001/status
```
