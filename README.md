# RollupWatchdog — consensus-backed L2 health oracle

A GenLayer Intelligent Contract that watches L2 rollups and publishes a
**consensus-backed health verdict** — `HEALTHY`, `DEGRADED`, `HALTED`, or
`INDETERMINATE` — with a plain-language explanation. Targets **Studio Next**
(v0.3.0 namespace, chain 61997).

## The problem

L2 sequencers are centralized single points of failure. When one stalls, users
can't transact or withdraw, and bridges keep routing funds into a chain that is
effectively down. The only answers today are centralized status pages and
Discord posts.

"Is this rollup healthy?" is a *judgment*, not a computation: no blocks for 40
minutes could be an outage or a quiet hour. An LLM weighing block freshness
against the official status page makes that call better than a threshold alert,
and validator consensus makes it trustless — which is what GenLayer is for.

Generic uptime monitors watch APIs and infrastructure. RollupWatchdog is
**L2-native**: it reasons about sequencer liveness, block-production freshness,
and official chain status channels — signals, failure modes, and consumers
(bridges, wallets) that generic infra monitoring doesn't cover.

## How it works

1. Anyone calls `assess(chain_id)`.
2. Each validator independently fetches:
   - a **trusted clock** — the latest block of two independent Blockscout
     deployments, whichever is later, used to *measure* how stale the L2's
     latest block is,
   - the **L2's latest block** via that chain's Blockscout API,
   - the chain's **official status page**, where one exists.
3. The contract computes block age itself. The LLM judges **only** the status
   and writes the summary; it is never asked for numbers.
4. `gl.eq_principle.prompt_comparative` drives consensus: `status` and
   `block_age_bucket` must match, the sampled numbers are excluded from
   comparison, and summaries must reach the same qualitative conclusion.
5. The verdict is stored on-chain, readable via `get_assessment(chain_id)`.

Chains are configured at deploy time, so this is a reusable oracle framework
rather than a hardcoded demo; more can be added via owner-only `add_chain`.

## Repo layout

```
rollup_watchdog.py         # the Intelligent Contract (single file, GenVM)
test_rollup_watchdog.py    # off-chain tests (stubbed SDK, no dependencies)
index.html                 # dashboard (single static file, no build step)
```

## Design decisions

**Every number is measured, not generated.** An LLM has no clock, so asking it
"how old is this block?" yields a plausible guess — and block age *is* the
liveness signal. The contract subtracts timestamps itself and hands the model
the result as a fact. The model's output is reduced to `status` + `summary` and
re-assembled into a fixed key set, so it cannot inject fields or unbounded data
into storage.

**The status page is hostile input.** Whoever controls or spoofs it would
otherwise have a free-form channel into the prompt. So: only
`status.indicator` and `status.description` are extracted (≤200 chars, JSON
escaped), they are fenced and labelled untrusted, and a measured block age over
30 minutes structurally blocks `HEALTHY` regardless of what the text claims.
The status page can downgrade a verdict; it cannot upgrade one.

**A missing signal is not a bad signal.** An unreadable status page is reported
to the model as *missing*, and the rules state that this is never by itself
grounds for `DEGRADED`.

**"I can't tell" is a valid answer.** If the L2's latest block can't be read,
the verdict is `INDETERMINATE` rather than a guess or a revert. Every verdict
carries `assessed_at_unix`, because a three-week-old `HEALTHY` is the dangerous
reading for a bridge.

**Blockscout, not Etherscan — a consensus requirement.** Etherscan rate-limits
per API key, and every validator runs the same code at the same moment with the
same key, so validator fan-out *guarantees* throttling. On Bradbury this made
the leader succeed while every validator hit `Max calls per sec rate limit
reached` and disagreed, sending the transaction `UNDETERMINED`. Blockscout is
keyless and limits per IP, so each validator draws from its own budget. The
general lesson: a shared credential is a shared bottleneck, and N-validator
fan-out turns a rate limit into a consensus failure.

**Assessments are throttled** to one per chain per 60 seconds, and a call
inside that window is rejected rather than served a cached verdict — the oracle
never implies it re-checked when it didn't.

## Contract API

| Method | Type | Description |
|---|---|---|
| `assess(chain_id)` | write | Run a consensus assessment; stores + returns verdict JSON |
| `get_assessment(chain_id)` | view | Latest verdict (`""` if never assessed) |
| `get_assessment_count()` | view | Total assessments since deployment |
| `list_chains()` | view | Configured chains (canonical JSON list) |
| `add_chain(chain_id, name, blockscout_url, status_url)` | write, owner-only | Register a new chain |

### Verdict JSON

A real verdict, read back from the deployed contract via `get_assessment("base")`
(keys are sorted, so validators compare identical strings):

```json
{
  "assessed_at_unix": 1789474811,
  "block_age_bucket": "<1min",
  "block_age_seconds": 0,
  "block_number": 51342737,
  "status": "HEALTHY",
  "summary": "Block production is current with a 0-second gap, and the third-party status page reports all systems operational. No signals indicate an outage or degradation."
}
```

| Field | Source | Notes |
|---|---|---|
| `status` | LLM judgment | `HEALTHY` \| `DEGRADED` \| `HALTED` \| `INDETERMINATE` |
| `summary` | LLM prose | ≤280 chars |
| `block_number` | Blockscout | `null` when `INDETERMINATE` |
| `block_age_seconds` | measured | `null` when `INDETERMINATE` |
| `block_age_bucket` | measured | `<1min` \| `1-5min` \| `5-30min` \| `>30min` \| `UNKNOWN`; validators compare this exactly |
| `assessed_at_unix` | wall clock | Check before trusting a verdict |

## Deploy

One constructor argument, `chains_json`. No API key — the data sources are
keyless by design.

```json
[
  {"id": "base", "name": "Base",
   "blockscout_url": "https://base.blockscout.com",
   "status_url": "https://status.base.org/api/v2/status.json"},
  {"id": "arbitrum-one", "name": "Arbitrum One",
   "blockscout_url": "https://arbitrum.blockscout.com", "status_url": ""},
  {"id": "zksync-era", "name": "ZKsync Era",
   "blockscout_url": "https://zksync.blockscout.com", "status_url": ""}
]
```

Enforced at deploy time: `id`/`name` non-empty and `id` unique;
`blockscout_url` an `https://` URL with no `?`, `&` or spaces (which would let
a config value rewrite the request); `status_url` either `""` or `https://`.

Verify each URL in a browser first. Most L2 status pages are Atlassian-hosted
at `<host>/api/v2/status.json`, but not all: `status.base.org` serves it,
`status.arbitrum.io` returns 404. A chain with `status_url: ""` is assessed on
block freshness alone — a supported configuration, not a degraded one.

**Current deployment**

- **Studio Next** (chain 61997): `0x9bB01d8B136527698f6196a28aD657e8800E22B7`
- Owner (deployer; the only account that can call `add_chain`):
  `0x4e2eb6e59d37b792AeAc7F0682b3Ff8fcbAc21Be`
- Chains: Base (block freshness + status page), Arbitrum One and ZKsync Era
  (block freshness only)

An earlier revision, identical in behaviour but using the pre-v0.3.0
namespace, ran on Bradbury testnet at
`0x4c780074870f2cDE322343FEDc0feAb166338923`.

## Dashboard

`index.html` is the entire frontend — one static file loading `genlayer-js`
from a CDN, so there is no build step and no `node_modules`.

`CONFIG.contract` at the top of the script block already points at the live
deployment — change it only if you deploy your own. Serve locally with
`python3 -m http.server`, or publish via **Settings → Pages → main / root**.

Writes follow the documented flow: `estimateTransactionFeesForWrite`, then
`writeContract` with `fees: { distribution, feeValue }`, then
`waitForDecision` and `isSuccessful`. Studio charges for execution and the
consensus contract rejects a zero fee, so the estimate is not optional. Note
that finalized does not imply succeeded — hence the `isSuccessful` check. The
SDK is pinned to `2.0.0-rc.1`; npm's `latest` (1.1.8) predates this fee model.

Every verdict shows how the validators voted. The contract cannot see its own
transaction hash, so the page lists the contract's assessments via
`sim_getTransactionsForAddress` and matches on `assessed_at_unix` — generated
once per execution, and both stored and returned, so the match is exact. The
tally is reported as cast (`3/5 agreed · 2 idle`), never rounded up to 5/5:
idle validators did not agree. A failed call means no votes, nothing worse.

Those votes come from the RPC, not the explorer. The link goes to the
contract's address page rather than the individual transaction, because the
explorer's `/tx/` route 404s even for hashes its own address page lists.

The page defines its own chain rather than using a bundled one. Each Studio
instance has a distinct chain id — **dev 61997, staging 61998, production
61999** — and `genlayer-js` ships `studionet` pointing at production, so it
cannot reach a dev deployment. The dashboard therefore targets
`https://studio-dev.genlayer.com/api` with `id: 61997` via `defineChain`. That
endpoint allows cross-origin requests, so GitHub Pages can call it directly.

Reading verdicts is free. **Running an assessment is a write and costs GEN**,
so the page generates a session account and keeps its key in `localStorage`,
which makes the address stable across reloads and therefore fundable. A **Fund
10 GEN** button calls the dev faucet (`sim_fundAccount`, the same JSON-RPC
method Studio's own button uses) and the balance is shown next to it. Testnet
only — a key in `localStorage` is readable by any script on the origin.

The chain list comes from `list_chains()` rather than being hardcoded, so the
page reflects the actual deployment and labels each chain one-signal or two.
The verdict panel shows the traffic light, the AI-written reason, and the
measured numbers. A stored verdict describes a past moment, so the panel is
headed with when it was assessed and says the figures below belong to that
moment — otherwise "the latest block was produced 5 seconds ago" reads as five
seconds before page load, when it may be hours old.

## Tests

```bash
python3 test_rollup_watchdog.py    # 68 tests, no dependencies
pytest test_rollup_watchdog.py     # if pytest is available
```

The contract only *executes* inside GenVM, so the suite stubs the `genlayer`
namespace and covers everything that is plain Python: Blockscout response
shapes and error bodies, ISO-8601 conversion, status-page extraction, config
validation, retry and throttle logic, and verdict assembly. It pins the
security-relevant behaviour — measured numbers override the model's, a stale
chain cannot be `HEALTHY`, and only two short status-page fields reach the
prompt — and mirrors the real SDK shapes, so the three GenVM behaviours in
*Targeting Studio Next* are regression-locked.

`_civil_to_unix()` is checked against `calendar.timegm` over 20,000 dates plus
leap-year and century edge cases.

Not covered: consensus, storage, and nondet isolation. Deploy to Studio for
those.

## Targeting Studio Next (v0.3.0)

Porting between builds touches these lines and no others:

| | Pre-v0.3.0 | Studio Next |
|---|---|---|
| Header | `Depends` line alone | `# v0.3.0` line above it |
| Dependency | any pinned hash | exact hash; `:latest` rejected on-network |
| Import | `from genlayer import *` | `import genlayer as gl` + `from genlayer.types import *` |
| Base class | `gl.Contract` | `gl.contract.Contract` |
| `TreeMap` | auto-imported | `gl.storage.TreeMap` |
| Error type | (absent) | `gl.vm.UserError` |

Unchanged: `gl.message.sender_address`, `gl.public.view` / `write`,
`gl.nondet.exec_prompt(..., response_format="json")`, and
`gl.eq_principle.prompt_comparative`. Stored integers must be `u256`/`i256`.

A namespace mismatch gives no useful message — the schema loader executes the
module, so an import-time failure surfaces as *"Could not load contract
schema"* with empty stdout and stderr.

Note that the published contract docs still show the pre-v0.3.0 style
(`from genlayer import *`, `gl.Contract`, bare `TreeMap`) and pin the older
`py-genlayer` hash. The version here is the one that actually deploys and runs
on Studio Next. Everything else the docs describe — `gl.nondet.web.get()`
returning an object with `.body`, `exec_prompt(..., response_format="json")`,
`gl.eq_principle.prompt_comparative`, `gl.vm.UserError` — matches.

Three GenVM behaviours the docs don't mention, each found by deploying:

- **`gl.nondet.web.get()` returns a `Response`, not a string.** `json.loads()`
  on it raises `TypeError`. `_response_text()` unwraps it, and names the
  attributes it did find when the shape is unfamiliar.
- **`exec_prompt(..., response_format="json")` returns a parsed dict**, so
  `json.loads()` on it also raises. `_parse_model_reply()` takes dict/str/bytes.
- **The error type moves between builds.** Hardcoding a name is worse than it
  sounds: the wrong one raises `AttributeError` *while handling* the real
  error, so the traceback blames the error type instead of the cause.
  `_resolve_error_type()` resolves it by dotted path.

## Verified on-chain

Confirmed on **Studio Next** (chain 61997). Every transaction reached
`FINALIZED` / `SUCCESS` with `rotation_count: 0` — no leader rotations.

- **Deployment** `0x1b61fe8a…5fa669` — `SUCCESS`, 5 validators, 0 rotations.
- **The clock fix, measured** — `assess("base")` `0x9b2fac72…6a2c75` stamped
  `assessed_at_unix` at 22:10:00Z, the same second the transaction was created.
  `eth.blockscout.com` was 44–59 minutes behind that day, so a single-source
  clock would have stamped it ~45 minutes stale. It also confirms GenVM follows
  the gnosis redirect from inside validators.
- **A measured block age** — `assess("zksync-era")` returned `HEALTHY` with
  `block_age_seconds: 15` on block 71998859, and the verdict cited it: *"the
  latest block … arriving just 15 seconds ago."* Under the old single-clock
  code this read `0` for every chain, because the stale clock drove the age
  negative and `max(0, …)` hid it. Fifteen seconds is evidence; zero was not.
  The same verdict noted *"No status page was available for this chain"*, so
  the missing-signal rule holds on this deployment too.
- **Granularity, not the bug** — `assess("arbitrum-one")` on the same
  deployment minutes later returned `block_age_seconds: 0`. That is correct:
  Arbitrum produces ~4 blocks/second, so its newest block is younger than the
  clock's own newest block and the age clamps. The verdict age on that read was
  27s, which proves the clock was current — a stale clock would have shown
  ~2700s. Two chains, one clock, different answers because they produce blocks
  at different rates.

Earlier runs on the previous deployment (`0x3D62e1a4…163F45`) established the
rest, though their `block_age_seconds: 0` is the old clamp hiding that lagging
clock, not a measured zero:

- **Namespace** `0x1c2f49d8…1d801b` — the v0.3.0 namespace loads and runs.
- **Two signals** — `assess("base")` `0xe6367270…cf8f4` returned `HEALTHY`,
  citing both: *"Block production is current with a 0-second gap, and the
  third-party status page reports all systems operational."*
- **One signal** — `assess("arbitrum-one")` `0xafbd5cb2…975a2` returned
  `HEALTHY` on a chain with no status page: *"The latest block was produced
  just now… The official status page was unavailable, but this alone does not
  indicate a problem."* That is the missing-signal rule holding in production.
- **Views** — `get_assessment("base")` returns the stored verdict and
  `get_assessment_count()` returns `2`, so `u256` works as a view return type.
- **Dashboard write** — `0x31606536…33cdb2`, submitted from the browser session
  account, reached `FINALIZED` / `Accepted` with `rotation_count: 0` and a
  transaction value of `0 GEN`: the fee travels in `fees`, not `value`.

The same behaviour was confirmed earlier on Bradbury under the pre-v0.3.0
namespace. A minority validator may still vote Disagree — one did there, on an
otherwise unanimous verdict. That is ordinary model variance on a prose
comparison, and quorum absorbs it.

Two lessons came from reading live verdicts rather than from tests:

- **Equivalence criteria must match how fast the data moves.** The first
  version allowed block numbers to differ by "a few blocks"; Arbitrum produces
  ~4 per second and rounds start ~90s apart, so every validator succeeded and
  then correctly voted Disagree. The criteria now exclude sampled numbers.
- **A missing signal must not look like a bad reading.** The placeholder
  `{"indicator": "UNAVAILABLE"}` sat in statuspage.io's own vocabulary, so the
  model read it as trouble and returned `DEGRADED` for a chain that had
  produced a block 0 seconds earlier.

## Known limitations

- **Blockscout is a single data source, and its two roles fail differently.**
  If a chain's own Blockscout is unreachable the verdict is `INDETERMINATE` and
  is stored. If *every* clock source is unreachable the call fails outright and
  nothing is stored — without a trusted clock there is no honest verdict to
  write.
- **Timestamps are assumed UTC.** An instance returning a non-UTC offset would
  be mis-read by that offset. Nothing else is timezone-dependent: the contract
  works in Unix seconds and never reads a validator's local clock, and the
  dashboard compares `Date.now()` (epoch-based) against `assessed_at_unix`, so
  verdict age is identical worldwide. It does assume the viewer's device clock
  is roughly correct; a clock more than two minutes behind is reported rather
  than silently shown as a fresh verdict.
- **Block age is only accurate to one clock block (~12s).** An L2 block newer
  than the clock's latest clamps to `0`, so ages under ~12s are not meaningfully
  distinguishable. Ample for the >30min decisions this oracle makes, but
  `block_age_seconds: 0` means "at least as fresh as the clock", not "produced
  this instant".
- **The clock is a real dependency.** On 2026-09-15 `eth.blockscout.com` ran
  59 minutes behind while `gnosis.blockscout.com` ran 1 minute behind. A single
  lagging source reports an old "now", which would have marked any outage
  shorter than the lag as `HEALTHY`. Hence two sources, later reading wins, and
  skew past `CLOCK_SKEW_TOLERANCE_SECONDS` returns `INDETERMINATE` instead of a
  clamp. The gnosis URL redirects to gnosisscan.io but serves correct Blockscout
  JSON; substitute another Blockscout deployment if validators are throttled.
- **The 30-minute `HEALTHY` floor is blunt.** It bounds prompt injection rather
  than modelling any chain; a genuinely sparse rollup would read `DEGRADED`.
- **Verdicts are overwritten, not appended.** No history yet.
- **Only two status-page fields are read**, discarding richer per-component
  data as the price of a narrow injection surface.
- **Validators can disagree about whether a status page is reachable**, which
  remains a source of minority disagreement.

## Roadmap

- Verdict history per chain (trend view: "degraded 3× this month")
- Alert subscriptions on status change
- A second liveness source so Blockscout isn't a single point of failure
- L1 batch-proving pipeline signals (commit → prove → execute stalls)

## Links

- Live dashboard: https://aplombird-x.github.io/rollup-watchdog/
- Contract on Studio Next:
  https://explorer-studio-dev.genlayer.com/address/0x9bB01d8B136527698f6196a28aD657e8800E22B7
- Demo video: ← add
- Agent Tank submission: ← add
- Built for the GenLayer Agent Tank hackathon, Sep 2026.
