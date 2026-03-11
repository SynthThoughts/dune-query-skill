---
name: dune-query-skill
description: "Enhanced Dune Analytics workflow integrating Spellbook knowledge and semantic table search. Use when user asks to analyze on-chain data, write Dune queries, explore blockchain metrics, find the right Dune tables, or asks about DeFi/NFT/token analytics. Triggers on: 'query dex volume', 'analyze NFT trades', 'find lending data', 'Dune query for X', 'on-chain analysis', 'blockchain metrics', 'token transfers', 'protocol TVL', 'gas analysis', 'airdrop tracking', 'whale analysis'. Do NOT use for: general SQL questions unrelated to Dune, non-blockchain data analysis."
license: Apache-2.0
metadata:
  author: mfer
  version: "2.0.0"
---

# Dune Query Skill

Enhance Dune MCP with Spellbook domain knowledge + local semantic search for smarter on-chain analysis.

## Architecture

```
User Query → Intent Classification → Table Discovery → Query Construction → Execution
                                          ↓
                                   ┌──────┴──────┐
                                   │  Has Index?  │
                                   └──────┬──────┘
                                    Yes   │   No
                              ┌───────────┼───────────┐
                              ↓                       ↓
                    Semantic Search            MCP searchTables
                    (local ChromaDB)          (keyword + pagerank)
                              ↓                       ↓
                    Precise table set         Noisy results → manual filter
```

## Semantic Search Setup

Script: `scripts/dune_table_indexer.py`
Requires: `pip install chromadb`

### Step 1: Crawl tables via Dune MCP

For a target protocol (e.g., Kamino), use MCP to fetch ALL tables with schema:

```
mcp__dune__searchTables(query="<protocol>", categories=["decoded","spell"],
                        includeSchema=true, includeMetadata=true, limit=50, offset=0)
→ paginate until all results collected
→ write combined results array to: data/<protocol>_tables.json
```

### Step 2: Build semantic index

```bash
python scripts/dune_table_indexer.py index \
  --input data/<protocol>_tables.json \
  --collection <protocol>
```

The indexer automatically:
- Parses table names into semantic actions (borrow, deposit, withdraw, etc.)
- Extracts financial columns (amount, liquidity, collateral, usd)
- Classifies table function (borrow/deposit/withdraw/admin/maintenance/etc.)
- Filters out boilerplate columns from embedding

### Step 3: Semantic search for queries

```bash
# Search all collections (decoded + spellbook), auto-selects best embedding
python scripts/dune_table_indexer.py search "large borrow transactions" -c all

# Search specific collection
python scripts/dune_table_indexer.py search "large borrow transactions" -c decoded

# With filters
python scripts/dune_table_indexer.py search "deposit liquidity" -c all --has-amount -f deposit

# JSON output for programmatic use
python scripts/dune_table_indexer.py search "liquidation events" -c all --json
```

Embedding auto-selection priority:
1. **Vertex AI SDK** (free, uses ADC credentials) → searches `*_gemini` collections
2. **Gemini REST API** (needs `GEMINI_API_KEY` env var) → searches `*_gemini` collections
3. **Local MiniLM** (no API needed, lower quality) → searches local collections

Filter options:
- `--category`: spell, decoded, canonical, community
- `--abi-type`: event, call
- `--function`: borrow, deposit, withdraw, repay, liquidation, flashloan, swap, admin_init, admin_config, maintenance, transfer, invest, trade, fees_rewards
- `--has-amount`: only tables with financial columns

## Workflow

**Two modes based on Dune API key availability:**

| Mode | Dune MCP | Capability |
|------|----------|------------|
| **Full (API key)** | Available | Table discovery → SQL generation → Create query → Execute → Results → Visualization |
| **Offline (no key)** | Unavailable | Table discovery (local Skill) → SQL generation → Output code for user to run manually |

Detect mode: if `mcp__dune__*` tools are available, use Full mode. Otherwise, Offline mode.

### 1. Intent Classification

Map user intent to Spellbook **sector** + **scope**:

| Sector | Key Schemas | Typical Questions |
|--------|------------|-------------------|
| dex | `dex.trades`, `dex.pools`, `dex.prices` | DEX volume, swaps, liquidity, arbitrage |
| nft | `nft.trades`, `nft.mints` | NFT sales, floor price, collection stats |
| tokens | `tokens.erc20`, `tokens.transfers`, `prices.usd` | Token metadata, transfers, balances, prices |
| lending | `lending.borrow`, `lending.supply` | Borrow/supply rates, utilization, liquidations |
| gas | `gas.fees` | Gas costs, EIP-1559 analysis |
| labels | `labels.all` | Address classification, entity identification |
| bridges | `bridges.flows` | Cross-chain transfers |
| staking | `staking.flows` | Staking deposits/withdrawals |

For project-specific queries, look in both sector tables AND project-specific schemas.

### 2. Table Discovery (Adaptive Strategy)

Run Skill semantic search first to determine embedding quality, then decide trust level:

```
Step 1: Skill search
  python scripts/dune_table_indexer.py search "<user intent>" -c all --json
  → Check output: "embedding": "gemini" or "embedding": "local"

Step 2: Branch by embedding quality + MCP availability

  ┌─ Gemini embedding (high trust) ─────────────────────────────────┐
  │  Skill results are precise (similarity 0.6-0.8 = strong match)  │
  │  → Use Skill results as PRIMARY table selection                 │
  │  → [Full mode] MCP supplements: fetch schema for SQL writing    │
  │  → [Offline mode] Use Skill metadata + sector knowledge         │
  └─────────────────────────────────────────────────────────────────┘

  ┌─ Local MiniLM embedding (low trust) ────────────────────────────┐
  │  Skill results are noisy (similarity 0.3-0.5 = weak signal)    │
  │  → [Full mode] MCP as PRIMARY table selection                   │
  │    mcp__dune__searchTables(query="<keywords>",                  │
  │      categories=["spell"], includeSchema=true)                  │
  │    Skill results as secondary reference only                    │
  │  → [Offline mode] Use Skill results + sector knowledge to       │
  │    infer table names, note lower confidence in output           │
  └─────────────────────────────────────────────────────────────────┘
```

**Trust calibration:**
- Gemini similarity ≥ 0.60 → high confidence, table is relevant
- Gemini similarity 0.45-0.60 → moderate, cross-check with MCP if available
- Local MiniLM → always cross-check with MCP if available
- MCP page_rank ≥ 5.0 → widely used table, prefer over low-pagerank alternatives

**Fallback (no semantic index, Full mode only):**
```
Step 1: mcp__dune__searchTables (categories: ["spell"] first, then ["decoded"])
Step 2: If contract-specific → mcp__dune__searchTablesByContractAddress
```

### 3. Query Construction

Load `references/query-patterns.md` for SQL templates by sector.

**Critical DuneSQL rules:**
- Always filter on partition columns (`block_date`, `block_month`, `block_time`)
- Use `WHERE block_date >= CURRENT_DATE - INTERVAL '7' DAY` (not `NOW()`)
- DuneSQL is Trino-based: use `APPROX_DISTINCT()`, `TRY_CAST()`, `FROM_UNIXTIME()`
- Cross-chain queries: use `blockchain` column to filter/group
- Token amounts: always divide by `POWER(10, decimals)` — raw values are in wei/lamports
- USD values: join with `prices.usd` on `(blockchain, contract_address, minute)`
- Solana addresses: use `from_base58()` to convert varchar → varbinary for price joins

### 4. Execution & Visualization

**Full mode (Dune API key available):**
```
Step 1: mcp__dune__createDuneQuery → get query_id
Step 2: mcp__dune__executeQueryById → get execution_id
Step 3: mcp__dune__getExecutionResults → get data
Step 4: mcp__dune__generateVisualization → create chart (if applicable)
```

**Offline mode (no API key):**
```
→ Output the generated SQL query as a code block
→ Tell user: "Paste this into https://dune.com/queries to run"
```

## Sector Knowledge

Load `references/sectors.md` for detailed sector knowledge when needed.

## Cross-Table Join Patterns

**Price enrichment (most common):**
```sql
SELECT t.*, p.price * t.amount / POWER(10, tok.decimals) AS amount_usd
FROM dex.trades t
LEFT JOIN prices.usd p ON p.blockchain = t.blockchain
  AND p.contract_address = t.token_bought_address
  AND p.minute = DATE_TRUNC('minute', t.block_time)
LEFT JOIN tokens.erc20 tok ON tok.blockchain = t.blockchain
  AND tok.contract_address = t.token_bought_address
```

**Solana price enrichment (base58 → varbinary):**
```sql
LEFT JOIN prices.usd p ON p.blockchain = 'solana'
  AND p.contract_address = from_base58(token_mint)
  AND p.minute = DATE_TRUNC('minute', block_time)
```

**Address labeling:**
```sql
LEFT JOIN labels.all l ON l.blockchain = t.blockchain AND l.address = t.trader
```

## Performance Checklist

Before executing any query, verify:
- [ ] Partition filter present (`block_date`, `block_month`)
- [ ] Time range is reasonable (avoid full table scans)
- [ ] `LIMIT` on exploratory queries
- [ ] `APPROX_DISTINCT` instead of `COUNT(DISTINCT)` for large datasets
- [ ] Joins use matching blockchain + address columns
- [ ] Token amounts properly decimalized
