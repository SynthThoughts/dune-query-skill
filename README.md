# Dune Query Skill

An AI skill that enhances on-chain data analysis by combining **semantic table search** with **Dune Analytics MCP**. Pre-indexed 10,000+ Dune tables (decoded contracts + Spellbook models) so your AI assistant can find the right tables and write accurate DuneSQL queries.

## What It Does

```
"Show me Aave borrow transactions over $1M this week"

  → Semantic search finds: aave_ethereum.pool_evt_borrow, lending.borrow, aave.borrow
  → Picks best table based on embedding quality + page_rank
  → Generates DuneSQL with correct partition filters, decimal handling, price joins
  → [With API key] Creates & executes query on Dune, returns results
  → [Without API key] Outputs SQL for you to paste into dune.com
```

## How It Works

### Two Operating Modes

| Mode | Requirements | What Happens |
|------|-------------|--------------|
| **Full** | Dune API key + Dune MCP | Find tables → Generate SQL → Execute on Dune → Return results & charts |
| **Offline** | This skill only | Find tables → Generate SQL → Output code for manual execution |

### Adaptive Table Discovery

The skill outputs an `embedding` field indicating search quality, which controls trust level:

| Embedding | Similarity Range | Trust Level | Strategy |
|-----------|-----------------|-------------|----------|
| **gemini** | 0.60-0.80 | High | Skill results are primary; MCP supplements with schema |
| **gemini** | 0.45-0.60 | Moderate | Cross-check with MCP |
| **local** | 0.30-0.50 | Low | MCP is primary (if available); Skill is secondary reference |

```
User: "aave lending borrow"

Gemini embedding (high trust):
  aave_borrow                          0.744  ← precise match
  aave_ethereum_borrow                 0.738
  lending_ethereum_base_borrow         0.725

Local MiniLM (low trust):
  kamino_solana.klend_evt_borrow       0.487  ← cross-protocol noise
  aave.borrow                          0.451
  lending_ethereum_base_borrow         0.412
```

### Embedding Priority

Query-time embedding is automatically selected:

1. **Vertex AI SDK** — free, uses Google Cloud ADC credentials (`~/.config/gcloud/application_default_credentials.json`)
2. **Gemini REST API** — needs `GEMINI_API_KEY` environment variable
3. **Local MiniLM** — no API needed, runs offline, lower quality

The pre-built index ships with both Gemini (768d) and MiniLM (384d) embeddings. Gemini embeddings are used when a compatible query embedding is available; otherwise falls back to local.

## Repository Structure

```
dune-query-skill/
├── SKILL.md                          # AI instruction file (loaded by Claude Code / Cursor / etc.)
├── scripts/
│   └── dune_table_indexer.py         # Semantic search engine (index, search, list)
├── references/
│   ├── query-patterns.md             # DuneSQL templates by sector (DEX, lending, NFT, etc.)
│   └── sectors.md                    # Spellbook sector reference (table schemas, partitions)
└── data/
    └── chroma_db/                    # Pre-built ChromaDB vector index (Git LFS)
        ├── chroma.sqlite3            # Metadata + embeddings store (~65MB)
        ├── *.config.json             # Collection configs (embedding type, dimensions)
        └── <uuid>/                   # HNSW index files (4 collections)
```

### Pre-built Index Contents

| Collection | Items | Source | Embedding |
|-----------|-------|--------|-----------|
| `decoded` | 2,139 | Decoded contract events/calls (Kamino, Aave, etc.) | MiniLM 384d |
| `decoded_gemini` | 2,139 | Same tables, Gemini embeddings | Gemini 768d |
| `spellbook` | 7,973 | Spellbook models (all sectors, all chains) | MiniLM 384d |
| `spellbook_gemini` | 7,973 | Same models, Gemini embeddings | Gemini 768d |

**Decoded tables breakdown** (2,139 tables):
- Events: 1,039 | Swaps: 174 | Deposits: 40 | Borrows: 12 | Withdrawals: 35
- Admin/config: 72 | Fees/rewards: 37 | Transfers: 6 | Other: 724

**Spellbook models breakdown** (7,973 models):
- Swaps: 1,584 | Transfers: 865 | Deposits: 581 | Pools: 531 | Balances: 411
- Fees/rewards: 249 | Withdrawals: 163 | Flashloans: 140 | Borrows: 130 | Bridges: 92
- Airdrops: 95 | Prices: 92 | Mints: 34 | Governance: 20 | Other: 2,974

Each table entry stores: table name, category, ABI type, function classification, financial column flags, and page_rank score. No raw document text is stored (embeddings + metadata only, optimized to 109MB).

### Key Files

**`SKILL.md`** — The instruction file that AI assistants read. Contains:
- Intent classification (sector → table mapping)
- Adaptive table discovery strategy (Gemini vs local trust levels)
- DuneSQL rules (partitions, decimals, price joins, Solana base58)
- Full vs Offline mode logic
- Cross-table join patterns

**`scripts/dune_table_indexer.py`** — CLI tool with three commands:
- `index` — Build/update the ChromaDB index from crawled table JSON
- `search` — Semantic search with filters (`--category`, `--function`, `--has-amount`, `--json`)
- `list` — Show all indexed collections with stats

**`references/`** — Domain knowledge loaded on demand:
- `query-patterns.md` — DuneSQL templates (time functions, aggregations, CTEs)
- `sectors.md` — Spellbook sector schemas (DEX, lending, NFT, tokens, gas, bridges, staking)

## Installation

### Prerequisites

```bash
pip install chromadb
```

Optional (for Gemini-quality search):
- Google Cloud ADC: `gcloud auth application-default login` (free, uses Vertex AI SDK)
- Or set `GEMINI_API_KEY` environment variable

### Claude Code

```bash
# Install the skill
claude skill add --from https://github.com/SynthThoughts/dune-query-skill

# For full mode, also add Dune MCP:
claude mcp add dune -e DUNE_API_KEY=your_key -- npx -y @duneanalytics/mcp-server
```

### Cursor / Windsurf / Other AI IDEs

Add to your project's `.cursor/rules` or equivalent AI instruction file:

```
Read and follow the instructions in path/to/dune-query-skill/SKILL.md
```

Then ensure the `scripts/` and `data/` directories are accessible from your project.

### AI-Friendly Installation Command

Paste this to any AI assistant to have it set up the skill automatically:

```
Clone https://github.com/SynthThoughts/dune-query-skill to ~/.claude/skills/dune-query-skill (or your preferred location). Then:

1. Run: pip install chromadb
2. Verify the index: python scripts/dune_table_indexer.py list
   — Should show 4 collections: decoded (2139), decoded_gemini (2139), spellbook (7973), spellbook_gemini (7973)
3. Test search: python scripts/dune_table_indexer.py search "DEX trading volume" -c all
   — Should return tables like dex.trades, dex_aggregator.trades with similarity scores
4. Read SKILL.md and follow its workflow for all future on-chain data analysis requests

Optional Gemini embedding setup (higher quality search):
- If user has gcloud CLI: gcloud auth application-default login
- Or set GEMINI_API_KEY env var
- Verify: python scripts/dune_table_indexer.py search "aave borrow" -c all
  — Output should show "Embedding: gemini" (not "local")

Optional Dune MCP setup (for query execution):
- Need a Dune API key from https://dune.com/settings/api
- Claude Code: claude mcp add dune -e DUNE_API_KEY=<key> -- npx -y @duneanalytics/mcp-server
- Other: add Dune MCP server to your MCP config
```

## Usage Examples

### Direct CLI Search

```bash
# Find lending tables
python scripts/dune_table_indexer.py search "lending borrow repay" -c all

# Find DEX tables with financial columns only
python scripts/dune_table_indexer.py search "swap volume" -c all --has-amount

# JSON output for programmatic use
python scripts/dune_table_indexer.py search "NFT marketplace sales" -c all --json

# Filter by function type
python scripts/dune_table_indexer.py search "liquidity" -c all -f deposit
```

### Through AI Assistant

Just ask naturally:

- "Query the weekly active users on Aave, Morpho, and Compound"
- "Show me Kamino borrow transactions over $1M in the past 24 hours"
- "Compare DEX volume across Ethereum, Arbitrum, and Base"
- "Find all liquidation events on lending protocols this month"

The skill handles table discovery, SQL generation, and (with Dune MCP) execution automatically.

## Extending the Index

### Add More Decoded Tables

Use Dune MCP to crawl a protocol's decoded tables, then index them:

```bash
# 1. Crawl via MCP (in your AI assistant)
# mcp__dune__searchTables(query="uniswap", categories=["decoded"],
#     includeSchema=true, includeMetadata=true, limit=50)
# → Save results to data/uniswap_tables.json

# 2. Index locally
python scripts/dune_table_indexer.py index \
  --input data/uniswap_tables.json \
  --collection decoded

# 3. Build Gemini embeddings (optional, needs Vertex AI or Gemini API)
python scripts/build_gemini_index.py
```

### Rebuild Spellbook Index

The spellbook index is built from the [Spellbook repository](https://github.com/duneanalytics/spellbook):

```bash
git clone https://github.com/duneanalytics/spellbook /tmp/spellbook
python scripts/dune_table_indexer.py index --spellbook /tmp/spellbook
```

## Architecture Deep Dive

### Query Flow

```
User Query
    │
    ├─ 1. Intent Classification
    │     Map to sector (dex/lending/nft/tokens/...) + scope (specific protocol?)
    │
    ├─ 2. Table Discovery
    │     ├─ Skill: semantic search → ranked tables with similarity scores
    │     │   Output includes "embedding": "gemini" or "local"
    │     │
    │     ├─ [Full mode] MCP: keyword search → tables with full column schema
    │     │
    │     └─ Merge: Gemini high-trust → Skill leads; Local low-trust → MCP leads
    │
    ├─ 3. Query Construction
    │     Apply DuneSQL rules: partition filters, decimal handling, price joins
    │     Reference: query-patterns.md + sectors.md
    │
    └─ 4. Execution
          [Full]    createQuery → execute → getResults → visualize
          [Offline] Output SQL code block
```

### Embedding Architecture

```
Index time (pre-computed, shipped in repo):
  Table metadata → Semantic document → [MiniLM-L6 (384d)] → decoded/spellbook collections
                                     → [Gemini-001 (768d)] → decoded_gemini/spellbook_gemini collections

Query time (computed on each search):
  User query → [Best available embedder] → Compare against matching collection
               Vertex AI SDK (free)  ─┐
               Gemini REST API ───────┤→ Search *_gemini collections
               Local MiniLM ──────────→ Search local collections
```

### Semantic Document Construction

Each table is converted to a searchable document:

```
Table: kamino_solana.klend_evt_borrowobligationliquidity
→ Document: "kamino klend borrow obligation liquidity | columns: owner reserve
   liquidity_amount collateral_exchange_rate | function: borrow | has_amount: true"
```

The indexer:
- Parses CamelCase/snake_case table names into semantic tokens
- Extracts financial columns (amount, liquidity, collateral, usd, price)
- Classifies function (borrow/deposit/withdraw/swap/...) via keyword matching
- Filters boilerplate columns (evt_index, call_block_time, etc.)

## License

Apache-2.0
