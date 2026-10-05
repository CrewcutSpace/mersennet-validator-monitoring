# Mersennet Validator Monitoring

Prometheus exporter, alert rules and a Grafana dashboard for [Mersennet](https://docs.mersennet.com/) validators.

The node's own `/metrics` shows the chain (height, finalization, mempool), but not **your validator**:
whether it is active, benched or jailed, how many leader slots it missed this epoch, its stake and rank,
and whether it runs the current release. This kit adds exactly that, and alerts you the moment it changes.

| | |
|---|---|
| **Exporter** | Reads `mersennet_validatorSet` from your node and from the public RPC, plus `mersennet_nodeIdentity` and the official release list. Python standard library only — runs as a Docker container or a systemd service. |
| **Alerts** | Node down, height stalled, not finalizing, fork / state mismatch, behind the network, 2+ missed slots in an epoch, benched, jailed, not active, new release available. |
| **Dashboard** | Status, active set, build, benched, rank, epoch / blocks to the next one, proposed / missed slots, stake, node health and trends. |

## Requirements

- A Mersennet node with its RPC enabled (the official installer serves it on `127.0.0.1:8545`).
- Prometheus (and Alertmanager for notifications), Grafana 10+.
- Your **node identity**:

```bash
curl -s localhost:8545 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"mersennet_nodeIdentity","params":[]}'
# → "identity": "0x…"   (use it in lowercase)
```

## 1. Run the exporter

### Docker

```bash
docker run -d --name mersennet-exporter --restart unless-stopped --network host \
  -e MERSENNET_IDENTITY=0x<your node identity> \
  ghcr.io/crewcutspace/mersennet-validator-exporter:main
```

`--network host` lets the container reach the node RPC on `127.0.0.1:8545`. Build the image yourself with
`docker build -t mersennet-validator-exporter exporter/` if you prefer.

### systemd (next to the official installer)

```bash
sudo install -m 0755 exporter/mersennet_exporter.py /usr/local/bin/mersennet_exporter.py
sudo install -m 0644 systemd/mersennet-exporter.service /etc/systemd/system/
sudo systemctl edit mersennet-exporter    # set Environment=MERSENNET_IDENTITY=0x…
sudo systemctl daemon-reload && sudo systemctl enable --now mersennet-exporter
```

### Check

```bash
curl -s localhost:7979/metrics | grep -E 'validator_(registered|rank)|node_outdated'
```

| Variable | Default | |
|---|---|---|
| `MERSENNET_IDENTITY` | — | node identity, required |
| `MERSENNET_LOCAL_RPC` | `http://127.0.0.1:8545` | your node |
| `MERSENNET_PUBLIC_RPC` | `https://rpc.mersennet.com` | reference for status and lag |
| `LISTEN_ADDR` | `0.0.0.0:7979` | open `7979` to your Prometheus only |

## 2. Prometheus

Add the two jobs from [`prometheus/scrape.yml`](prometheus/scrape.yml) and load the rules:

```yaml
rule_files:
  - /etc/prometheus/rules/mersennet-alerts.yml   # prometheus/alerts.yml from this repo
```

The node metrics are served on the RPC port under `/metrics`. Keep `8545` closed to the internet;
scrape it from the same host or open it to the Prometheus server only.

## 3. Grafana

**Dashboards → New → Import → Upload JSON file** → [`grafana/mersennet-validator.json`](grafana/mersennet-validator.json),
then pick your Prometheus datasource.

## 4. Logs (optional)

[`loki/rules.yml`](loki/rules.yml) has three log-based alerts (watchdog restart, slashing evidence on the network,
leader rounds given up) for a Loki ruler, if you ship the node journal to Loki.

## Metrics

Every series of the exporter carries `source="local"` (your node) or `source="public"` (the public RPC).
Validator status is read from `source="public"`, so it stays correct while your node is down.

| Metric | Meaning |
|---|---|
| `mersennet_validator_registered{status}` | 1 while registered; `status` = pending / active / standby / jailed / exiting |
| `mersennet_validator_in_active_set` | 1 while in the active set this epoch |
| `mersennet_validator_benched` | 1 while benched |
| `mersennet_validator_proposed_slots`, `_missed_slots` | leader slots this epoch |
| `mersennet_validator_total_proposed` | blocks proposed since registration |
| `mersennet_validator_jailed_until_epoch`, `_times_jailed` | jail |
| `mersennet_validator_self_stake_mrsn`, `_delegated_mrsn`, `_voting_stake_mrsn` | stake, MRSN |
| `mersennet_validator_rank` | place by voting stake (1 = largest; ties: earlier registration first, as on the explorer) |
| `mersennet_validator_commission_bps` | commission |
| `mersennet_validatorset_height`, `_epoch`, `_next_epoch_at` | chain position seen by each source |
| `mersennet_validatorset_validators`, `_active`, `_max_active` | registered, active, active-set limit |
| `mersennet_validatorset_active_min_stake_mrsn`, `_active_stake_mrsn` | smallest and total stake in the active set |
| `mersennet_node_version_info{version,rev}` | release the node runs |
| `mersennet_release_latest_info{rev}` | current official release ([downloads/SHA256SUMS](https://mersennet.com/downloads/SHA256SUMS), every 10 min) |
| `mersennet_node_outdated` | 1 when the node does not run the current release |
| `mersennet_validatorset_up` | 0 when an RPC did not answer |

## How the alerts read

- **Missed slots / benched / jailed:** a single missed slot now and then is normal — the leader hands the height to the failover leader when the previous block arrives late — so the alert fires only when one more miss would bench the validator (2+ missed and the next one would reach 10% of the slots proposed). 3 missed leader slots in an epoch (at least 10% of the slots proposed) bench the validator; benched or >20% missed means jailed for the next epoch, longer when it repeats. No stake is lost. Restart a validator only late in an epoch.
- **New release:** a release usually comes with a switch height — update before it, or the node forks off. The height is announced on [explorer.mersennet.com/upgrades](https://explorer.mersennet.com/upgrades) and in the Mersennet Telegram.
- **Behind the network:** the two heights are read a moment apart; the alert corrects for that, so a few blocks of difference is normal.

## License

MIT — by [CrewcutSpace](https://crewcut.space). Issues and pull requests welcome.
