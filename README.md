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

## Installation Modes

Choose based on what API keys you have:

| Mode | Download | Best For | Requires |
|------|----------|----------|----------|
| **Lite** | ~50KB (repo only) | Have Dune API key, MCP handles table discovery | Dune MCP |
| **Standard** | +18MB | Want offline semantic search, no external API | Nothing extra |
| **Full** | +56MB | Want highest precision search | Gemini API key or Google Cloud ADC |

### Quick Start

```bash
git clone https://github.com/SynthThoughts/dune-query-skill
cd dune-query-skill
pip install chromadb
./setup.sh          # Interactive: choose Lite / Standard / Full
```

### Claude Code

```bash
# 1. Clone and setup
git clone https://github.com/SynthThoughts/dune-query-skill ~/.claude/skills/dune-query-skill
cd ~/.claude/skills/dune-query-skill
pip install chromadb
./setup.sh

# 2. (Optional) Add Dune MCP for query execution
claude mcp add dune -e DUNE_API_KEY=your_key -- npx -y @duneanalytics/mcp-server
```

### Cursor / Windsurf / Other AI IDEs

```bash
git clone https://github.com/SynthThoughts/dune-query-skill path/to/dune-query-skill
cd path/to/dune-query-skill
pip install chromadb
./setup.sh
```

Then add to your AI instruction file (`.cursor/rules`, etc.):
```
Read and follow the instructions in path/to/dune-query-skill/SKILL.md
```

### AI-Friendly Installation Command

Paste this to any AI assistant to set up the skill automatically:

```
Install the Dune Query Skill from https://github.com/SynthThoughts/dune-query-skill:

1. git clone https://github.com/SynthThoughts/dune-query-skill ~/.claude/skills/dune-query-skill
2. cd ~/.claude/skills/dune-query-skill && pip install chromadb
3. Run ./setup.sh — choose mode based on available API keys:
   - Lite (have Dune API key, MCP handles table discovery)
   - Standard (want offline search, no API needed)
   - Full (have Gemini API key or Google Cloud ADC for highest quality)
4. Verify: python3 scripts/dune_table_indexer.py list
5. Test: python3 scripts/dune_table_indexer.py search "DEX trading volume" -c all
6. Read SKILL.md and follow its workflow for all on-chain data analysis

Optional Dune MCP (for executing queries, not just generating SQL):
  claude mcp add dune -e DUNE_API_KEY=<key> -- npx -y @duneanalytics/mcp-server

Optional Gemini (for high-quality search, Full mode only):
  gcloud auth application-default login   # or export GEMINI_API_KEY=your_key
```

## How It Works

### Operating Modes

| Capability | Lite (MCP only) | Standard (MiniLM) | Full (Gemini) |
|-----------|-----------------|-------------------|---------------|
| Table discovery | MCP keyword search | Local semantic search | High-precision semantic search |
| SQL generation | Yes | Yes | Yes |
| Query execution | Yes (via MCP) | No (output SQL) | No (output SQL) |
| + Dune MCP | — | Yes (via MCP) | Yes (via MCP) |
| Offline capable | No | Yes | No (needs API at query time) |

### Adaptive Table Discovery

The skill outputs an `embedding` field indicating search quality, which controls trust level:

```
User: "aave lending borrow"

Gemini embedding (high trust, similarity 0.6-0.8):
  aave_borrow                          0.744  ← precise match
  aave_ethereum_borrow                 0.738
  lending_ethereum_base_borrow         0.725
  → Skill results are PRIMARY; MCP supplements with schema

Local MiniLM (low trust, similarity 0.3-0.5):
  kamino_solana.klend_evt_borrow       0.487  ← cross-protocol noise
  aave.borrow                          0.451
  lending_ethereum_base_borrow         0.412
  → MCP is PRIMARY (if available); Skill is secondary reference
```

**Trust calibration:**
- Gemini similarity >= 0.60 → high confidence, table is relevant
- Gemini similarity 0.45-0.60 → moderate, cross-check with MCP
- Local MiniLM → always cross-check with MCP if available
- MCP page_rank >= 5.0 → widely used table, prefer over low-pagerank alternatives

### Embedding Priority

Query-time embedding is automatically selected:

1. **Vertex AI SDK** — free, uses Google Cloud ADC (`gcloud auth application-default login`)
2. **Gemini REST API** — needs `GEMINI_API_KEY` environment variable
3. **Local MiniLM** — no API needed, runs fully offline, lower quality

## Repository Structure

```
dune-query-skill/
├── SKILL.md                          # AI instruction file (the "brain")
├── setup.sh                          # Interactive installer (choose Lite/Standard/Full)
├── scripts/
│   └── dune_table_indexer.py         # Semantic search engine (index, search, list)
├── references/
│   ├── query-patterns.md             # DuneSQL templates by sector
│   └── sectors.md                    # Spellbook sector reference
└── data/
    └── chroma_db/                    # Vector index (downloaded via setup.sh)
```

### Pre-built Index Contents

| Collection | Items | Source | Embedding | In Standard | In Full |
|-----------|-------|--------|-----------|:-----------:|:-------:|
| `decoded` | 2,139 | Contract events/calls | MiniLM 384d | Yes | Yes |
| `spellbook` | 7,973 | Spellbook models | MiniLM 384d | Yes | Yes |
| `decoded_gemini` | 2,139 | Same tables | Gemini 768d | — | Yes |
| `spellbook_gemini` | 7,973 | Same models | Gemini 768d | — | Yes |

**Decoded tables** (2,139): Events 1,039 · Swaps 174 · Deposits 40 · Borrows 12 · Withdrawals 35 · Admin 72 · Fees 37 · Other 730

**Spellbook models** (7,973): Swaps 1,584 · Transfers 865 · Deposits 581 · Pools 531 · Balances 411 · Fees 249 · Withdrawals 163 · Flashloans 140 · Borrows 130 · Bridges 92 · Airdrops 95 · Prices 92 · Other 3,028

Each entry stores: embeddings, table name, category, ABI type, function classification, financial column flags, page_rank. No raw document text (metadata-only, optimized for size).

### Key Files

**`SKILL.md`** — AI instruction file containing:
- Intent classification (sector → table mapping)
- Adaptive table discovery (Gemini vs local trust levels)
- DuneSQL rules (partitions, decimals, price joins, Solana base58)
- Full vs Offline mode switching
- Cross-table join patterns

**`scripts/dune_table_indexer.py`** — CLI with three commands:
- `index` — Build/update ChromaDB from crawled table JSON
- `search` — Semantic search with filters (`--category`, `--function`, `--has-amount`, `--json`)
- `list` — Show all indexed collections with stats

**`references/`** — Domain knowledge loaded on demand:
- `query-patterns.md` — DuneSQL templates (time functions, aggregations, CTEs)
- `sectors.md` — Sector schemas (DEX, lending, NFT, tokens, gas, bridges, staking)

## Usage Examples

### CLI Search

```bash
# Find lending tables
python3 scripts/dune_table_indexer.py search "lending borrow repay" -c all

# DEX tables with financial columns only
python3 scripts/dune_table_indexer.py search "swap volume" -c all --has-amount

# JSON output for programmatic use
python3 scripts/dune_table_indexer.py search "NFT marketplace sales" -c all --json

# Filter by function type
python3 scripts/dune_table_indexer.py search "liquidity" -c all -f deposit
```

### Through AI Assistant

Just ask naturally:

- "Query the weekly active users on Aave, Morpho, and Compound"
- "Show me Kamino borrow transactions over $1M in the past 24 hours"
- "Compare DEX volume across Ethereum, Arbitrum, and Base"
- "Find all liquidation events on lending protocols this month"

## Extending the Index

### Add Decoded Tables for a Protocol

```bash
# 1. Crawl via Dune MCP (in your AI assistant):
# mcp__dune__searchTables(query="uniswap", categories=["decoded"],
#     includeSchema=true, includeMetadata=true, limit=50)
# → Save results to data/uniswap_tables.json

# 2. Index
python3 scripts/dune_table_indexer.py index \
  --input data/uniswap_tables.json --collection decoded
```

### Rebuild Spellbook Index

```bash
git clone https://github.com/duneanalytics/spellbook /tmp/spellbook
python3 scripts/dune_table_indexer.py index --spellbook /tmp/spellbook
```

## Architecture

```
User Query
    │
    ├─ 1. Intent Classification
    │     Map to sector (dex/lending/nft/tokens/...) + scope
    │
    ├─ 2. Table Discovery (adaptive)
    │     ├─ Skill semantic search → ranked tables + "embedding" type
    │     ├─ [Gemini] Skill leads, MCP supplements schema
    │     ├─ [Local]  MCP leads, Skill supplements
    │     └─ [Lite]   MCP only
    │
    ├─ 3. Query Construction
    │     DuneSQL: partition filters, decimals, price joins
    │     Templates: query-patterns.md + sectors.md
    │
    └─ 4. Execution
          [With MCP]    createQuery → execute → results → chart
          [Without MCP] Output SQL code block → user runs on dune.com
```

### Embedding Architecture

```
Index time (pre-computed, downloaded via setup.sh):
  Table metadata → Semantic doc → MiniLM-L6 (384d) → decoded / spellbook
                                → Gemini-001 (768d) → decoded_gemini / spellbook_gemini

Query time (per search):
  User query → Best available embedder:
               Vertex AI SDK (free) ──┐
               Gemini REST API ───────┤→ *_gemini collections (high quality)
               Local MiniLM ──────────→ local collections (offline fallback)
```

## License

Apache-2.0
