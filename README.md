# RollupWatchdog — consensus-backed L2 health oracle

An Intelligent Contract on GenLayer (Bradbury testnet) that watches L2 rollups
and publishes a **consensus-backed health verdict** — `HEALTHY`, `DEGRADED`,
or `HALTED` — with a plain-language explanation.

## The problem it solves

L2 sequencers are centralized single points of failure. When one stalls,
users can't transact or withdraw, and bridges/wallets keep routing funds into
a chain that's effectively down. Today there is no trust-minimized source of
"is this rollup healthy right now?" — only centralized status pages and
Discord announcements. RollupWatchdog turns chain health into an **on-chain
oracle verdict** that five independent validators must agree on, so any
wallet, bridge, or dashboard can consume it without trusting a single party.

"Is this rollup healthy?" is a *judgment*, not a computation: no batches for
40 minutes could be an outage or just a quiet hour. An LLM weighing block
freshness against the chain's official status page makes that call far better
than a threshold alert — and validator consensus makes it trustless. That is
exactly what GenLayer exists for.

## How it works

1. Anyone calls `assess(chain_id)`.
2. Validators independently fetch **live signals**:
   - latest-block freshness via the Etherscan V2 multichain API
     (a live, authoritative liveness signal), and
   - the chain's official status page (statuspage.io public API, where one exists).
3. An LLM synthesizes the signals into a verdict JSON
   (`status`, `summary`, `block_number`, `block_age_seconds`).
4. `gl.eq_principle.prompt_comparative` drives consensus: the `status` must
   match across validators; summaries must describe the same key facts.
5. The consensus verdict is stored on-chain and readable via
   `get_assessment(chain_id)`.

Chains are configured at deploy time (constructor arg), so the contract is a
**reusable oracle framework** — new chains can be added later via the
owner-only `add_chain` method.

## Contract API

| Method | Type | Description |
|---|---|---|
| `assess(chain_id)` | write | Run a consensus assessment; stores + returns verdict JSON |
| `get_assessment(chain_id)` | view | Latest consensus verdict (`""` if never assessed) |
| `list_chains()` | view | Configured chains |
| `add_chain(...)` | write, owner-only | Register a new chain |

Deployed on **Bradbury testnet** (chain ID 4221):
- Contract: `0x…` ← fill after deploy
- Constructor chains: Arbitrum One, Base, ZKsync Era

## Frontend

`frontend/` is a minimal dashboard (genlayer-js): pick a chain, hit **Run
assessment**, watch the full transaction lifecycle (sign → pending → confirmed),
and see the traffic-light verdict with the AI-written rationale. It also polls
`get_assessment` so the latest consensus verdict is always visible.

Run it:

```bash
cd frontend
npm install
npm run dev
```

## Repo layout

```
contracts/rollup_watchdog.py   # the Intelligent Contract (single file, GenVM)
frontend/                      # dashboard (genlayer-js + wallet)
docs/                          # submission notes, demo video link
```

## Differentiation

Generic uptime monitors (e.g. the "Uptime" project in the GenLayer ecosystem)
watch APIs and infrastructure. RollupWatchdog is **L2-native**: it reasons
about sequencer liveness, block-production freshness, and official chain
status channels — signals, failure modes, and consumers (bridges, wallets)
that generic infra monitoring doesn't cover.

## Roadmap (post-hackathon)

- Verdict history per chain (trend view: "degraded 3× this month")
- Alert subscriptions: notify when a chain's status changes
- L1 batch-proving pipeline signals (commit → prove → execute stalls)
- More chains via `add_chain`

## Links

- Demo video: ← add
- Agent Tank submission: ← add
- Built for the GenLayer Agent Tank hackathon, Sep 2026.
