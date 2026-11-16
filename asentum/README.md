# Asentum Validator Exporter

Prometheus exporter for an [Asentum](https://testnet.asentum.com) validator.

The Asentum node has no `/metrics`. This exporter reads the node's HTTP API and the public one and
exports the node (height, finality, last block age, lag behind the network) and your validator: bond,
status, committee state and membership, missed votes, participation and wallet balance.

Python standard library only.

## Run

```bash
docker run -d --name asentum-exporter --restart unless-stopped --network host \
  -e ASENTUM_ADDRESS=ase1<your validator address> \
  ghcr.io/crewcutspace/asentum-validator-exporter:main
```

The validator address is printed by the node on start (`[validator] address: ase1…`).

```bash
curl -s localhost:7980/metrics | grep -E 'validator_(registered|in_committee|stake)'
```

| Variable | Default | |
|---|---|---|
| `ASENTUM_ADDRESS` | — | validator address, `ase1…` or `0x…`, required |
| `ASENTUM_LOCAL_RPC` | `http://127.0.0.1:8545` | your node |
| `ASENTUM_PUBLIC_RPC` | `https://testnet.asentum.com` | reference for the network tip, the validator set and the committee |
| `LISTEN_ADDR` | `0.0.0.0:7980` | open `7980` to your Prometheus only |
| `ASENTUM_RELEASE` | — | release your node runs, e.g. `0.9.5`; the node API does not report it. Enables `asentum_node_outdated` |
| `ASENTUM_RELEASE_URL` | `https://testnet.asentum.com/install/validator-release.json` | current official release, the file the installer reads |

The validator set and the committee come from the public RPC, so they stay visible while your node is
down. Participation comes from your node: it scans the last 1000 blocks. Scrape every 60 s.

## Metrics

| Metric | |
|---|---|
| `asentum_node_up`, `asentum_node_ready` | the node answers `/ready` and reports itself ready |
| `asentum_node_height`, `asentum_node_tip`, `asentum_node_finalized` | heights of your node |
| `asentum_node_last_block_age_seconds` | seconds since the node's last block |
| `asentum_node_mempool_size` | transactions in the mempool |
| `asentum_network_up`, `asentum_network_tip`, `asentum_network_finalized` | the network, from the public RPC |
| `asentum_committee_epoch`, `asentum_committee_size` | current epoch and committee size |
| `asentum_validators_bonded` | bonded validators |
| `asentum_validators_by_participation{status}` | signing / idle / dormant validators |
| `asentum_validator_registered` | your validator is bonded |
| `asentum_validator_info{status,committee_state}` | bond status and committee state |
| `asentum_validator_active` | bond status is `active` |
| `asentum_validator_in_committee` | in the committee this epoch |
| `asentum_validator_stake_ase`, `asentum_validator_balance_ase` | bond and wallet balance, ASE |
| `asentum_validator_bonded_at` | height of the bond |
| `asentum_validator_next_committee_entry_at` | height of the next committee entry attempt |
| `asentum_validator_consecutive_misses` | consecutive missed votes |
| `asentum_validator_last_voted_height`, `asentum_validator_last_signed_block` | last vote and last signed block |
| `asentum_validator_signed_blocks`, `asentum_validator_participation_ratio` | signed blocks and share in the lookback |
| `asentum_release_latest_info{release,published_at}` | current official release, re-read every 10 min |
| `asentum_node_release_info{release}`, `asentum_node_outdated` | release of your node (`ASENTUM_RELEASE`) and 1 when it is not the current one |
