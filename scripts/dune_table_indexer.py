#!/usr/bin/env python3
"""
Dune Table Semantic Search

Build and query a semantic index of Dune tables.
Supports dual embedding: always builds local index, optionally builds Gemini index.
Search auto-selects the best available backend.

Usage:
    # Build index from Dune MCP crawled JSON (local + gemini if key available)
    python dune_table_indexer.py index -i tables.json -c kamino

    # Build index from Spellbook repo schema.yml files
    python dune_table_indexer.py index-spellbook /path/to/spellbook -c spellbook

    # Force local-only (skip Gemini even if key exists)
    python dune_table_indexer.py index -i tables.json -c kamino --embedding local

    # Semantic search (auto-selects best available embedding)
    python dune_table_indexer.py search "大额借款交易" -c kamino
    python dune_table_indexer.py search "DEX trading volume" -c spellbook

    # List indexed collections
    python dune_table_indexer.py list
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
CHROMA_DIR = os.path.join(DATA_DIR, 'chroma_db')

GEMINI_MODEL = 'gemini-embedding-001'
GEMINI_API_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}'
GEMINI_DIMENSIONS = 768


# ─── OAuth2 / ADC Support ────────────────────────────────────────────────────

ADC_PATHS = [
    os.path.expanduser('~/.config/gcloud/application_default_credentials.json'),
]

class OAuth2Token:
    """Manages OAuth2 access token from authorized_user credentials."""

    def __init__(self, creds: dict):
        self.client_id = creds['client_id']
        self.client_secret = creds['client_secret']
        self.refresh_token = creds['refresh_token']
        self._access_token = None
        self._expires_at = 0

    def get_access_token(self) -> str:
        """Get a valid access token, refreshing if needed."""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token

        payload = json.dumps({
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        }).encode('utf-8')

        req = Request('https://oauth2.googleapis.com/token', data=payload,
                      method='POST', headers={'Content-Type': 'application/json'})
        with urlopen(req) as resp:
            result = json.loads(resp.read())

        self._access_token = result['access_token']
        self._expires_at = time.time() + result.get('expires_in', 3600)
        return self._access_token


def _load_adc() -> OAuth2Token | None:
    """Load Application Default Credentials if available."""
    # Check env var first
    env_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    paths = ([env_path] if env_path else []) + ADC_PATHS

    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                creds = json.load(f)
            if creds.get('type') == 'authorized_user' and creds.get('refresh_token'):
                return OAuth2Token(creds)
        except Exception:
            continue
    return None


# ─── Gemini Embedding Function for ChromaDB ───────────────────────────────────

class GeminiEmbeddingFunction:
    """ChromaDB-compatible embedding function using Google Gemini API.

    Supports two auth modes:
    - API Key: x-goog-api-key header (free tier: 1000 req/day)
    - OAuth2: Bearer token from ADC (uses Google account quota, no daily limit)
    """

    def __init__(self, api_key: str = None, oauth2: OAuth2Token = None,
                 task_type: str = 'RETRIEVAL_DOCUMENT',
                 dimensions: int = GEMINI_DIMENSIONS):
        if not api_key and not oauth2:
            raise ValueError("Either api_key or oauth2 must be provided")
        self.api_key = api_key
        self.oauth2 = oauth2
        self.task_type = task_type
        self.dimensions = dimensions

    @staticmethod
    def name() -> str:
        return 'gemini'

    def _auth_headers(self) -> dict:
        """Return auth headers based on available credentials."""
        if self.oauth2:
            token = self.oauth2.get_access_token()
            return {'Authorization': f'Bearer {token}'}
        return {'x-goog-api-key': self.api_key}

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """ChromaDB calls this for queries (vs __call__ for documents)."""
        return self.__call__(input)

    def __call__(self, input: list[str]) -> list[list[float]]:
        """Embed a list of texts. ChromaDB calls this with input=list[str]."""
        all_embeddings = []
        batch_size = 100
        for start in range(0, len(input), batch_size):
            batch = input[start:start + batch_size]
            embeddings = self._batch_embed(batch)
            all_embeddings.extend(embeddings)
            if start + batch_size < len(input):
                time.sleep(0.1)
        return all_embeddings

    def _batch_embed(self, texts: list[str]) -> list[list[float]]:
        """Call Gemini batchEmbedContents API with retry on rate limit."""
        url = f'{GEMINI_API_URL}:batchEmbedContents'
        requests_body = []
        for text in texts:
            requests_body.append({
                'model': f'models/{GEMINI_MODEL}',
                'content': {'parts': [{'text': text}]},
                'taskType': self.task_type,
                'outputDimensionality': self.dimensions,
            })
        payload = json.dumps({'requests': requests_body}).encode('utf-8')

        headers = {'Content-Type': 'application/json'}
        headers.update(self._auth_headers())

        max_retries = 3
        for attempt in range(max_retries):
            req = Request(url, data=payload, method='POST', headers=headers)
            try:
                with urlopen(req) as resp:
                    result = json.loads(resp.read())
                return [emb['values'] for emb in result['embeddings']]
            except HTTPError as e:
                body = e.read().decode('utf-8', errors='replace')
                if e.code == 429 and attempt < max_retries - 1:
                    wait = 60
                    try:
                        err = json.loads(body)
                        for d in err.get('error', {}).get('details', []):
                            if 'retryDelay' in d:
                                delay_str = d['retryDelay'].rstrip('s')
                                wait = int(float(delay_str)) + 2
                    except Exception:
                        pass
                    print(f"\n  Rate limited, waiting {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    # Refresh headers in case token expired during wait
                    headers = {'Content-Type': 'application/json'}
                    headers.update(self._auth_headers())
                    continue
                print(f"Gemini API error {e.code}: {body}", file=sys.stderr)
                raise


def _get_gemini_auth(quiet: bool = False) -> tuple[str | None, OAuth2Token | None]:
    """Get Gemini credentials. Returns (api_key, oauth2) - one will be set.

    Priority: GEMINI_API_KEY env > OAuth2 ADC > 1Password API Key
    """
    # 1. API Key from env
    key = os.environ.get('GEMINI_API_KEY')
    if key:
        return key, None

    # 2. OAuth2 from ADC (free, no daily limit)
    oauth2 = _load_adc()
    if oauth2:
        return None, oauth2

    # 3. API Key from 1Password
    for path in ['op://AI/Google Gemini/apikey', 'op://AI/gemini/API KEY',
                  'op://AI/google/API KEY', 'op://AI/Google AI/API KEY']:
        try:
            result = subprocess.run(
                ['op', 'read', path],
                capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if quiet:
        return None, None
    print("Gemini credentials not found. Options:", file=sys.stderr)
    print("  1. Run: gcloud auth application-default login (recommended, free)", file=sys.stderr)
    print("  2. Set GEMINI_API_KEY env var", file=sys.stderr)
    print("  3. Add API key to 1Password (AI/Google Gemini/apikey)", file=sys.stderr)
    sys.exit(1)


def get_gemini_api_key(quiet: bool = False) -> str | None:
    """Legacy compat: returns API key or a sentinel if OAuth2 is available."""
    api_key, oauth2 = _get_gemini_auth(quiet=quiet)
    if api_key:
        return api_key
    if oauth2:
        return '__oauth2__'  # sentinel: OAuth2 available
    return None


# ─── Document & Metadata Building (MCP tables) ───────────────────────────────

def build_document(table: dict) -> str:
    """Create a rich text document for semantic embedding from table metadata."""
    parts = []
    full_name = table.get('full_name', '')
    parts.append(f"Table: {full_name}")

    name_parts = full_name.split('.')
    if len(name_parts) == 2:
        schema, table_name = name_parts
        parts.append(f"Schema: {schema}")
        for prefix in ['call_', 'evt_']:
            if prefix in table_name:
                action = table_name.split(prefix, 1)[1]
                action_words = re.sub(r'([a-z])([A-Z])', r'\1 \2', action)
                action_words = action_words.replace('_', ' ')
                parts.append(f"Action: {action_words}")
                break

    category = table.get('category', '')
    dataset_type = table.get('dataset_type', '')
    parts.append(f"Category: {category} ({dataset_type})")

    blockchains = table.get('blockchains', [])
    if blockchains:
        parts.append(f"Blockchains: {', '.join(blockchains)}")

    meta = table.get('metadata', {})
    if meta.get('description'):
        parts.append(f"Description: {meta['description']}")
    if meta.get('abi_type'):
        parts.append(f"ABI Type: {meta['abi_type']}")
    if meta.get('contract_name'):
        parts.append(f"Contract: {meta['contract_name']}")
    if meta.get('project_name'):
        parts.append(f"Project: {meta['project_name']}")
    if meta.get('page_rank_score'):
        parts.append(f"Popularity: {meta['page_rank_score']:.2f}")

    schema = table.get('schema', {})
    fields = schema.get('fields', [])
    if fields:
        boilerplate = {'call_block_slot', 'call_block_date', 'call_block_time',
                       'call_block_hash', 'call_tx_index', 'call_tx_id', 'call_tx_signer',
                       'call_inner_instruction_index', 'call_outer_instruction_index',
                       'call_inner_executing_account', 'call_outer_executing_account',
                       'call_executing_account', 'call_is_inner', 'call_program_name',
                       'call_instruction_name', 'call_version', 'call_data',
                       'call_account_arguments', 'call_inner_instructions',
                       'call_log_messages'}
        meaningful_cols = [f for f in fields if f['name'] not in boilerplate]
        if meaningful_cols:
            col_strs = [f"{f['name']} ({f['type']})" for f in meaningful_cols]
            parts.append(f"Key Columns: {', '.join(col_strs)}")

            amount_cols = [f['name'] for f in meaningful_cols
                          if any(kw in f['name'].lower()
                                 for kw in ['amount', 'liquidity', 'collateral', 'shares',
                                            'balance', 'fee', 'price', 'value', 'usd'])]
            if amount_cols:
                parts.append(f"Financial Columns: {', '.join(amount_cols)}")

            addr_cols = [f['name'] for f in meaningful_cols
                         if any(kw in f['name'].lower()
                                for kw in ['account_', 'owner', 'address', 'mint',
                                           'signer', 'borrower', 'depositor', 'liquidator'])]
            if addr_cols:
                parts.append(f"Address Columns: {', '.join(addr_cols)}")

    spell_meta = meta.get('spell_metadata', {})
    if spell_meta:
        if spell_meta.get('tags'):
            parts.append(f"Tags: {', '.join(spell_meta['tags'])}")
        if spell_meta.get('columns'):
            col_descs = [f"{c['column']}: {c.get('description', '')}"
                         for c in spell_meta['columns'] if c.get('description')]
            if col_descs:
                parts.append(f"Column Descriptions: {'; '.join(col_descs[:15])}")
        if spell_meta.get('depends_on'):
            parts.append(f"Depends On: {', '.join(spell_meta['depends_on'][:5])}")

    partition_cols = table.get('partition_columns', [])
    if partition_cols:
        parts.append(f"Partition Columns: {', '.join(partition_cols)}")

    return '\n'.join(parts)


def build_metadata(table: dict) -> dict:
    """Extract filterable metadata for ChromaDB."""
    meta = table.get('metadata', {})
    full_name = table.get('full_name', '')

    table_function = 'unknown'
    if '_call_' in full_name:
        name_lower = full_name.lower()
        func_map = [
            (['borrow', 'loan'], 'borrow'),
            (['deposit', 'supply'], 'deposit'),
            (['withdraw', 'redeem'], 'withdraw'),
            (['repay'], 'repay'),
            (['liquidat'], 'liquidation'),
            (['flash'], 'flashloan'),
            (['swap', 'trade', 'exchange'], 'swap'),
            (['init', 'create', 'setup'], 'admin_init'),
            (['update', 'set', 'config'], 'admin_config'),
            (['refresh'], 'maintenance'),
            (['transfer'], 'transfer'),
            (['invest'], 'invest'),
            (['buy', 'sell'], 'trade'),
            (['fee', 'reward'], 'fees_rewards'),
        ]
        for keywords, func in func_map:
            if any(kw in name_lower for kw in keywords):
                table_function = func
                break
        else:
            table_function = 'other'
    elif '_evt_' in full_name:
        table_function = 'event'

    schema = table.get('schema', {})
    fields = schema.get('fields', [])
    has_amount = any(any(kw in f['name'].lower()
                         for kw in ['amount', 'liquidity', 'collateral', 'shares', 'usd'])
                     for f in fields) if fields else False

    return {
        'full_name': full_name,
        'category': table.get('category', ''),
        'dataset_type': table.get('dataset_type', ''),
        'blockchains': ','.join(table.get('blockchains', [])),
        'abi_type': meta.get('abi_type', ''),
        'contract_name': meta.get('contract_name', ''),
        'project_name': meta.get('project_name', ''),
        'page_rank_score': float(meta.get('page_rank_score', 0)),
        'table_function': table_function,
        'has_amount': has_amount,
    }


# ─── Spellbook schema.yml Parsing ─────────────────────────────────────────────

def _extract_context_from_path(rel_path: str) -> tuple[str, str, str]:
    """Extract subproject, sector, project from a relative path."""
    parts = rel_path.split(os.sep)
    subproject = parts[0] if parts else ''
    sector = ''
    project = ''
    for i, p in enumerate(parts):
        if p == '_sector' and i + 1 < len(parts):
            sector = parts[i + 1]
        elif p == '_projects' and i + 1 < len(parts):
            project = parts[i + 1]
    return subproject, sector, project


def _parse_sql_config(sql_content: str) -> dict:
    """Extract metadata from dbt SQL config block and expose_spells."""
    info = {'schema': '', 'alias': '', 'blockchains': '', 'project': '', 'contributors': ''}

    # Extract config block: {{ config(...) }}
    config_match = re.search(r'\{\{\s*config\s*\((.*?)\)\s*\}\}', sql_content, re.DOTALL)
    if config_match:
        config_str = config_match.group(1)
        # schema
        m = re.search(r"schema\s*=\s*'([^']+)'", config_str)
        if m:
            info['schema'] = m.group(1)
        # alias
        m = re.search(r"alias\s*=\s*'([^']+)'", config_str)
        if m:
            info['alias'] = m.group(1)

    # Extract from expose_spells or hide_spells
    expose_match = re.search(r"expose_spells\s*\(\s*'(\[.*?\])'", sql_content, re.DOTALL)
    if expose_match:
        try:
            chains = json.loads(expose_match.group(1).replace("'", '"'))
            info['blockchains'] = ','.join(chains)
        except Exception:
            pass

    # Extract project from expose_spells
    proj_match = re.search(r'"project"\s*,\s*"(\w+)"', sql_content)
    if proj_match:
        info['project'] = proj_match.group(1)

    # Extract contributors
    contrib_match = re.search(r'contributors\s*=\s*\'(\[.*?\])\'', sql_content, re.DOTALL)
    if contrib_match:
        try:
            info['contributors'] = contrib_match.group(1)
        except Exception:
            pass

    # Extract SELECT column names (first SELECT block)
    select_match = re.search(r'\bSELECT\b(.*?)\bFROM\b', sql_content, re.DOTALL | re.IGNORECASE)
    if select_match:
        select_block = select_match.group(1)
        # Extract column aliases (AS col) or plain column names
        col_names = []
        for m in re.finditer(r'\bAS\s+(\w+)', select_block, re.IGNORECASE):
            col_names.append(m.group(1))
        if not col_names:
            for m in re.finditer(r'[\s,](\w+)\s*(?:,|$)', select_block):
                col_names.append(m.group(1))
        info['columns'] = col_names[:30]
    else:
        info['columns'] = []

    return info


def parse_spellbook_schemas(repo_path: str) -> list[dict]:
    """Parse all schema.yml + SQL model files from Spellbook repo."""
    import yaml

    base = os.path.join(repo_path, 'dbt_subprojects')
    models = []
    schema_defined_names = set()

    # Phase 1: Parse schema.yml files (rich metadata)
    for root, dirs, files in os.walk(base):
        for f in files:
            if f not in ('_schema.yml', 'schema.yml'):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, base)
            subproject, sector, project = _extract_context_from_path(rel)

            try:
                with open(path, 'r') as fh:
                    data = yaml.safe_load(fh)
            except Exception:
                continue
            if not data or 'models' not in data:
                continue

            for model in data['models']:
                name = model.get('name', '')
                if not name:
                    continue
                schema_defined_names.add(name)

                meta = model.get('meta', {})
                config = model.get('config', {})
                columns = model.get('columns', [])
                col_entries = [{'name': c.get('name', ''), 'description': c.get('description', '')}
                               for c in columns if c.get('name')]

                models.append({
                    'name': name,
                    'subproject': subproject,
                    'sector': sector or meta.get('sector', ''),
                    'project': project or '',
                    'blockchains': meta.get('blockchain', ''),
                    'description': model.get('description', meta.get('short_description', '')),
                    'short_description': meta.get('short_description', ''),
                    'tags': config.get('tags', []),
                    'contributors': meta.get('contributors', ''),
                    'columns': col_entries,
                    'schema_path': rel,
                })

    # Phase 2: Parse SQL files without schema definitions
    for root, dirs, files in os.walk(base):
        if '/models/' not in root + '/':
            continue
        for f in files:
            if not f.endswith('.sql'):
                continue
            model_name = f[:-4]  # strip .sql
            if model_name in schema_defined_names:
                continue

            path = os.path.join(root, f)
            rel = os.path.relpath(path, base)
            subproject, sector, project = _extract_context_from_path(rel)

            try:
                with open(path, 'r', errors='replace') as fh:
                    content = fh.read(8000)  # first 8KB enough for config
            except Exception:
                continue

            sql_info = _parse_sql_config(content)

            # Derive project from schema or path
            if not project and sql_info['schema']:
                # schema like 'omen_gnosis' → project = 'omen'
                schema_parts = sql_info['schema'].split('_')
                if len(schema_parts) >= 2:
                    project = schema_parts[0]
            if not project:
                # Try from path: models/projectname/chain/...
                path_parts = rel.split(os.sep)
                models_idx = next((i for i, p in enumerate(path_parts) if p == 'models'), -1)
                if models_idx >= 0 and models_idx + 1 < len(path_parts) - 1:
                    candidate = path_parts[models_idx + 1]
                    if not candidate.startswith('_'):
                        project = candidate

            # Build blockchains from sql_info or path
            blockchains = sql_info['blockchains']
            if not blockchains:
                # Try to detect chain from path
                path_lower = rel.lower()
                for chain in ['ethereum', 'polygon', 'arbitrum', 'optimism', 'base',
                              'bnb', 'avalanche_c', 'gnosis', 'fantom', 'solana', 'ton',
                              'celo', 'zksync', 'scroll', 'linea', 'blast']:
                    if f'/{chain}/' in path_lower or f'_{chain}.' in path_lower:
                        blockchains = chain
                        break

            col_entries = [{'name': c, 'description': ''} for c in sql_info.get('columns', [])]

            # Build description from schema.alias
            desc = ''
            if sql_info['schema'] and sql_info['alias']:
                desc = f"{sql_info['schema']}.{sql_info['alias']}"

            models.append({
                'name': model_name,
                'subproject': subproject,
                'sector': sector,
                'project': project,
                'blockchains': blockchains,
                'description': desc,
                'short_description': '',
                'tags': [],
                'contributors': sql_info.get('contributors', ''),
                'columns': col_entries,
                'schema_path': rel,
            })

    return models


def build_spellbook_document(model: dict) -> str:
    """Create embedding text from a Spellbook model definition."""
    parts = []
    parts.append(f"Model: {model['name']}")

    if model['sector']:
        parts.append(f"Sector: {model['sector']}")
    if model['project']:
        parts.append(f"Project: {model['project']}")
    parts.append(f"Subproject: {model['subproject']}")

    if model['blockchains']:
        parts.append(f"Blockchains: {model['blockchains']}")

    desc = model.get('short_description') or model.get('description', '')
    desc = re.sub(r'\{\{.*?\}\}', '', desc).strip()
    if desc:
        parts.append(f"Description: {desc}")

    if model['tags']:
        parts.append(f"Tags: {', '.join(model['tags'])}")

    cols_with_desc = [c for c in model['columns'] if c.get('description')]
    if cols_with_desc:
        col_strs = [f"{c['name']}: {c['description']}" for c in cols_with_desc[:20]]
        parts.append(f"Columns: {'; '.join(col_strs)}")

    col_names = [c['name'] for c in model['columns']]
    if col_names:
        parts.append(f"Column Names: {', '.join(col_names)}")

    return '\n'.join(parts)


def build_spellbook_metadata(model: dict) -> dict:
    """Build filterable metadata for a Spellbook model."""
    col_names = [c['name'] for c in model['columns']]
    has_amount = any(any(kw in n.lower()
                         for kw in ['amount', 'liquidity', 'collateral', 'shares', 'usd',
                                    'fee', 'price', 'value', 'volume', 'tvl', 'balance'])
                     for n in col_names)

    table_function = 'unknown'
    name_lower = model['name'].lower()
    func_map = [
        (['_trades', '_swaps'], 'swap'),
        (['_borrow', '_loan'], 'borrow'),
        (['_supply', '_deposit'], 'deposit'),
        (['_withdraw', '_redeem'], 'withdraw'),
        (['_repay'], 'repay'),
        (['_liquidat'], 'liquidation'),
        (['_flashloan', '_flash'], 'flashloan'),
        (['_pool', '_liquidity'], 'pool'),
        (['_transfer'], 'transfer'),
        (['_mint'], 'mint'),
        (['_price'], 'price'),
        (['_balance', '_portfolio'], 'balance'),
        (['_fee', '_revenue'], 'fees_rewards'),
        (['_bridge', '_flow'], 'bridge'),
        (['_vote', '_governance'], 'governance'),
        (['_airdrop', '_claim'], 'airdrop'),
    ]
    for keywords, func in func_map:
        if any(kw in name_lower for kw in keywords):
            table_function = func
            break

    return {
        'full_name': model['name'],
        'category': 'spell',
        'dataset_type': 'spellbook_model',
        'blockchains': model['blockchains'],
        'abi_type': '',
        'contract_name': '',
        'project_name': model['project'],
        'page_rank_score': 0.0,
        'table_function': table_function,
        'has_amount': has_amount,
        'sector': model['sector'],
        'subproject': model['subproject'],
    }


# ─── Shared Index Builder ─────────────────────────────────────────────────────

def _make_gemini_fn(task_type: str = 'RETRIEVAL_DOCUMENT',
                    dimensions: int = GEMINI_DIMENSIONS) -> GeminiEmbeddingFunction:
    """Create a GeminiEmbeddingFunction with best available auth."""
    api_key, oauth2 = _get_gemini_auth()
    auth_mode = 'OAuth2 (ADC)' if oauth2 else 'API Key'
    print(f"  Gemini auth: {auth_mode}", file=sys.stderr)
    return GeminiEmbeddingFunction(
        api_key=api_key, oauth2=oauth2,
        task_type=task_type, dimensions=dimensions,
    )


def _build_collection(client, collection_name: str, documents: list[str],
                      metadatas: list[dict], ids: list[str],
                      embedding: str, dimensions: int):
    """Build a single ChromaDB collection with the given embedding backend."""
    embedding_fn = None
    if embedding == 'gemini':
        embedding_fn = _make_gemini_fn('RETRIEVAL_DOCUMENT', dimensions)

    # Delete and recreate
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    create_kwargs = {
        'name': collection_name,
        'metadata': {
            'hnsw:space': 'cosine',
            'embedding_backend': embedding,
            'embedding_dimensions': dimensions if embedding == 'gemini' else 384,
        },
    }
    if embedding_fn:
        create_kwargs['embedding_function'] = embedding_fn
    collection = client.create_collection(**create_kwargs)

    batch_size = 50 if embedding == 'gemini' else 100
    total = len(documents)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        collection.add(
            documents=documents[start:end],
            metadatas=metadatas[start:end],
            ids=ids[start:end],
        )
        print(f"  [{embedding}] Indexed {end}/{total}...", end='\r')

    # Save config
    config_path = os.path.join(CHROMA_DIR, f'{collection_name}.config.json')
    with open(config_path, 'w') as f:
        json.dump({
            'embedding': embedding,
            'dimensions': dimensions if embedding == 'gemini' else 384,
        }, f)

    return total


def _build_dual_index(client, base_name: str, documents: list[str],
                      metadatas: list[dict], ids: list[str],
                      embedding_mode: str, dimensions: int):
    """Build local index (always) + gemini index (if available and requested)."""
    # Always build local
    print("Building local index (all-MiniLM-L6-v2, 384d)...")
    total = _build_collection(client, base_name, documents, metadatas, ids,
                              'local', dimensions)
    print(f"\n  Local: {total} items indexed")

    # Build Gemini if requested and credentials available
    if embedding_mode in ('gemini', 'both'):
        api_key, oauth2 = _get_gemini_auth(quiet=True)
        if api_key or oauth2:
            gemini_name = f"{base_name}_gemini"
            print(f"Building Gemini index ({GEMINI_MODEL}, {dimensions}d)...")
            try:
                _build_collection(client, gemini_name, documents, metadatas, ids,
                                  'gemini', dimensions)
                print(f"\n  Gemini: {total} items indexed")
            except Exception as e:
                print(f"\n  Gemini index failed ({e}), local index still available",
                      file=sys.stderr)
        else:
            print("  Gemini API key not found, skipping Gemini index")
            print("  (Set GEMINI_API_KEY or add to 1Password for better search quality)")

    return total


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_index(args):
    """Build semantic index from table JSON."""
    import chromadb

    with open(args.input, 'r') as f:
        data = json.load(f)

    tables = data if isinstance(data, list) else data.get('results', data.get('tables', []))
    if not tables:
        print("No tables found in input file.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    documents, metadatas, ids = [], [], []
    seen = set()
    for table in tables:
        full_name = table.get('full_name', '')
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        documents.append(build_document(table))
        metadatas.append(build_metadata(table))
        ids.append(full_name)

    total = _build_dual_index(client, args.collection, documents, metadatas, ids,
                              args.embedding, args.dimensions)

    func_counts = Counter(m['table_function'] for m in metadatas)
    amount_count = sum(1 for m in metadatas if m['has_amount'])
    print(f"\nSummary: {total} tables in '{args.collection}'")
    print(f"  By function: {dict(func_counts)}")
    print(f"  Tables with financial columns: {amount_count}")


def cmd_index_spellbook(args):
    """Build semantic index from Spellbook repo schema.yml files."""
    import chromadb

    repo_path = args.repo_path
    if not os.path.isdir(os.path.join(repo_path, 'dbt_subprojects')):
        print(f"Not a Spellbook repo: {repo_path} (missing dbt_subprojects/)", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing schema.yml files from {repo_path}...")
    models = parse_spellbook_schemas(repo_path)
    if not models:
        print("No models found.", file=sys.stderr)
        sys.exit(1)
    print(f"  Found {len(models)} model definitions")

    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    documents, metadatas, ids = [], [], []
    seen = set()
    for model in models:
        model_id = f"{model['subproject']}:{model['name']}"
        if model_id in seen:
            continue
        seen.add(model_id)
        documents.append(build_spellbook_document(model))
        metadatas.append(build_spellbook_metadata(model))
        ids.append(model_id)

    total = _build_dual_index(client, args.collection, documents, metadatas, ids,
                              args.embedding, args.dimensions)

    sector_counts = Counter(m['sector'] for m in metadatas if m['sector'])
    subproject_counts = Counter(m['subproject'] for m in metadatas)
    func_counts = Counter(m['table_function'] for m in metadatas)
    amount_count = sum(1 for m in metadatas if m['has_amount'])
    print(f"\nSummary: {total} models in '{args.collection}'")
    print(f"  By subproject: {dict(subproject_counts)}")
    print(f"  By sector: {dict(sector_counts)}")
    print(f"  By function: {dict(func_counts)}")
    print(f"  Models with financial columns: {amount_count}")


def _vertex_embed_query(text: str, dimensions: int = GEMINI_DIMENSIONS) -> list[float] | None:
    """Embed a single query using Vertex AI SDK. Returns None if unavailable."""
    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

        # Init Vertex AI (idempotent)
        adc = _load_adc()
        if not adc:
            return None
        # Read project from ADC file
        for path in ADC_PATHS:
            if os.path.exists(path):
                with open(path) as f:
                    creds = json.load(f)
                project = creds.get('quota_project_id', '')
                if project:
                    break
        else:
            return None

        vertexai.init(project=project, location='us-central1')
        model = TextEmbeddingModel.from_pretrained(GEMINI_MODEL)
        inputs = [TextEmbeddingInput(text=text, task_type='RETRIEVAL_QUERY')]
        embeddings = model.get_embeddings(inputs, output_dimensionality=dimensions)
        return embeddings[0].values
    except Exception:
        return None


def _get_best_collection(client, base_name: str, query_text: str = None):
    """Auto-select best available collection and optionally embed query.

    Returns (collection, query_embedding_or_None).
    If Gemini collection is selected and query_text provided, returns pre-computed
    query embedding for use with collection.query(query_embeddings=...).
    """
    gemini_name = f"{base_name}_gemini"
    gemini_config = os.path.join(CHROMA_DIR, f'{gemini_name}.config.json')

    # Try Gemini collection if it exists and we have credentials
    if os.path.exists(gemini_config):
        with open(gemini_config) as f:
            cfg = json.load(f)
        dims = cfg.get('dimensions', GEMINI_DIMENSIONS)

        # Method 1: Vertex AI SDK (free, preferred)
        if query_text:
            embedding = _vertex_embed_query(query_text, dims)
            if embedding:
                col = client.get_collection(gemini_name)
                print(f"  Using Gemini embedding (Vertex AI SDK)", file=sys.stderr)
                return col, embedding

        # Method 2: REST API with API Key or OAuth2
        api_key, oauth2 = _get_gemini_auth(quiet=True)
        if api_key or oauth2:
            try:
                embedding_fn = GeminiEmbeddingFunction(
                    api_key=api_key, oauth2=oauth2,
                    task_type='RETRIEVAL_QUERY', dimensions=dims,
                )
                col = client.get_collection(gemini_name, embedding_function=embedding_fn)
                auth_mode = 'OAuth2' if oauth2 else 'API Key'
                print(f"  Using Gemini embedding ({auth_mode})", file=sys.stderr)
                return col, None  # embedding_fn handles it
            except Exception:
                pass

    # Fall back to local collection
    local_config = os.path.join(CHROMA_DIR, f'{base_name}.config.json')
    if os.path.exists(local_config):
        col = client.get_collection(base_name)
        print(f"  Using local embedding", file=sys.stderr)
        return col, None

    # Try base name without config (legacy)
    return client.get_collection(base_name), None


def _search_collection(client, base_name: str, query: str, top_k: int,
                       where: dict = None, shared_embedding: list[float] = None) -> list[dict]:
    """Search a single collection, return list of result dicts."""
    # If we have a shared Gemini embedding, use it directly on the gemini collection
    if shared_embedding:
        gemini_name = f"{base_name}_gemini"
        try:
            collection = client.get_collection(gemini_name)
            query_embedding = shared_embedding
        except Exception:
            collection, query_embedding = None, None
        if not collection:
            try:
                collection, query_embedding = _get_best_collection(client, base_name, query_text=query)
            except Exception:
                return []
    else:
        try:
            collection, query_embedding = _get_best_collection(client, base_name, query_text=query)
        except Exception:
            return []

    query_kwargs = {
        'n_results': top_k,
        'where': where,
        'include': ['metadatas', 'distances'],
    }
    if query_embedding:
        query_kwargs['query_embeddings'] = [query_embedding]
    else:
        query_kwargs['query_texts'] = [query]

    results = collection.query(**query_kwargs)

    output = []
    for i in range(len(results['ids'][0])):
        meta = results['metadatas'][0][i]
        table_id = results['ids'][0][i]
        output.append({
            'table': table_id,
            'similarity': round(1 - results['distances'][0][i], 4),
            'function': meta.get('table_function', ''),
            'abi_type': meta.get('abi_type', ''),
            'has_amount': meta.get('has_amount', False),
            'page_rank': meta.get('page_rank_score', 0),
            'project': meta.get('project_name', ''),
            'source': base_name,
        })
    return output


def cmd_search(args):
    """Semantic search against the index."""
    import chromadb

    client = chromadb.PersistentClient(path=CHROMA_DIR)

    # Build where filter
    where_clauses = []
    if args.category:
        where_clauses.append({'category': args.category})
    if args.abi_type:
        where_clauses.append({'abi_type': args.abi_type})
    if args.function:
        where_clauses.append({'table_function': args.function})
    if args.has_amount:
        where_clauses.append({'has_amount': True})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {'$and': where_clauses}

    # Determine which collections to search
    if args.collection == 'all':
        collections_to_search = ['decoded', 'spellbook']
    else:
        collections_to_search = [args.collection]

    # Pre-compute Gemini query embedding once (shared across collections)
    # Priority: Vertex AI SDK (local/free) → REST API Key → None (fallback to local per-collection)
    shared_embedding = None
    embedding_type = 'local'  # Track which embedding is used for trust calibration
    gemini_dims = GEMINI_DIMENSIONS
    for coll_name in collections_to_search:
        gemini_config = os.path.join(CHROMA_DIR, f'{coll_name}_gemini.config.json')
        if os.path.exists(gemini_config):
            with open(gemini_config) as f:
                cfg = json.load(f)
            gemini_dims = cfg.get('dimensions', GEMINI_DIMENSIONS)
            break

    if any(os.path.exists(os.path.join(CHROMA_DIR, f'{c}_gemini.config.json'))
           for c in collections_to_search):
        # Try Vertex AI SDK first (free, no API key needed)
        shared_embedding = _vertex_embed_query(args.query, gemini_dims)
        if shared_embedding:
            embedding_type = 'gemini'
            print(f"  Query embedded via Vertex AI SDK", file=sys.stderr)
        else:
            # Try REST API with API Key or OAuth2
            api_key, oauth2 = _get_gemini_auth(quiet=True)
            if api_key or oauth2:
                try:
                    fn = GeminiEmbeddingFunction(
                        api_key=api_key, oauth2=oauth2,
                        task_type='RETRIEVAL_QUERY', dimensions=gemini_dims)
                    shared_embedding = fn([args.query])[0]
                    embedding_type = 'gemini'
                    auth_mode = 'API Key' if api_key else 'OAuth2'
                    print(f"  Query embedded via Gemini REST ({auth_mode})", file=sys.stderr)
                except Exception:
                    pass

    # Search all collections and merge
    all_results = []
    for coll_name in collections_to_search:
        results = _search_collection(client, coll_name, args.query, args.top_k, where,
                                     shared_embedding=shared_embedding)
        all_results.extend(results)

    # Deduplicate by table name (keep highest similarity)
    seen = {}
    for r in all_results:
        key = r['table']
        if key not in seen or r['similarity'] > seen[key]['similarity']:
            seen[key] = r
    output = list(seen.values())

    # Sort by similarity; page_rank shown as context for Claude to judge among close matches
    output.sort(key=lambda x: (-x['similarity'], -x['page_rank']))
    output = output[:args.top_k]

    # Clean up internal fields from JSON output
    for r in output:
        r.pop('score', None)

    if args.json:
        print(json.dumps({'embedding': embedding_type, 'results': output}, indent=2))
    else:
        print(f"Embedding: {embedding_type}")
        print(f"{'Table':<70} {'Sim':>5} {'Function':<12} {'Amt':>3} {'Rank':>5} {'Src':<10}")
        print('-' * 112)
        for e in output:
            amt = 'Y' if e['has_amount'] else ''
            rank = f"{e['page_rank']:.1f}" if e['page_rank'] > 0 else ''
            src = e.get('source', '')
            print(f"{e['table']:<70} {e['similarity']:>5.3f} "
                  f"{e['function']:<12} {amt:>3} {rank:>5} {src:<10}")


def cmd_list(args):
    """List all indexed collections."""
    import chromadb

    if not os.path.exists(CHROMA_DIR):
        print("No index found. Run 'index' first.")
        return

    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collections = client.list_collections()
    if not collections:
        print("No collections found.")
        return

    # Group by base name
    col_names = []
    for col_obj in collections:
        col_names.append(col_obj.name if hasattr(col_obj, 'name') else str(col_obj))

    base_names = set()
    for name in col_names:
        base_names.add(name.removesuffix('_gemini'))

    for base in sorted(base_names):
        variants = []
        for name in col_names:
            if name == base or name == f"{base}_gemini":
                col = client.get_collection(name)
                config_path = os.path.join(CHROMA_DIR, f'{name}.config.json')
                emb_info = 'local'
                if os.path.exists(config_path):
                    with open(config_path, 'r') as f:
                        cfg = json.load(f)
                    emb_info = f"{cfg.get('embedding', 'local')} ({cfg.get('dimensions', '?')}d)"
                variants.append(f"{emb_info}")

        col = client.get_collection(base)
        count = col.count()
        all_meta = col.get(include=['metadatas'])
        funcs = Counter(m.get('table_function', 'unknown') for m in all_meta['metadatas'])

        print(f"  {base}: {count} items [{' + '.join(variants)}]")
        for func, cnt in funcs.most_common():
            print(f"    {func}: {cnt}")


def main():
    parser = argparse.ArgumentParser(
        description='Dune Table Semantic Search - Smart table discovery for on-chain analysis')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Index (MCP tables)
    p_index = subparsers.add_parser('index', help='Build semantic index from table JSON')
    p_index.add_argument('--input', '-i', required=True, help='JSON file with table metadata')
    p_index.add_argument('--collection', '-c', default='default',
                         help='Collection name (e.g., kamino, dex, lending)')
    p_index.add_argument('--embedding', '-e', choices=['both', 'gemini', 'local'], default='both',
                         help='Embedding: both (default), gemini-only, or local-only')
    p_index.add_argument('--dimensions', '-d', type=int, default=GEMINI_DIMENSIONS,
                         help=f'Gemini embedding dimensions (default: {GEMINI_DIMENSIONS})')

    # Index Spellbook
    p_sb = subparsers.add_parser('index-spellbook',
                                  help='Build semantic index from Spellbook repo schema.yml')
    p_sb.add_argument('repo_path', help='Path to cloned Spellbook repo')
    p_sb.add_argument('--collection', '-c', default='spellbook',
                      help='Collection name (default: spellbook)')
    p_sb.add_argument('--embedding', '-e', choices=['both', 'gemini', 'local'], default='both',
                      help='Embedding: both (default), gemini-only, or local-only')
    p_sb.add_argument('--dimensions', '-d', type=int, default=GEMINI_DIMENSIONS,
                      help=f'Gemini embedding dimensions (default: {GEMINI_DIMENSIONS})')

    # Search
    p_search = subparsers.add_parser('search', help='Semantic search for tables')
    p_search.add_argument('query', help='Natural language query (supports Chinese)')
    p_search.add_argument('--collection', '-c', default='all',
                          help='Collection to search (decoded, spellbook, or "all" for both)')
    p_search.add_argument('--top-k', '-k', type=int, default=10, help='Number of results')
    p_search.add_argument('--category', help='Filter: spell, decoded, canonical, community')
    p_search.add_argument('--abi-type', help='Filter: event, call')
    p_search.add_argument('--function', '-f',
                          help='Filter: borrow, deposit, withdraw, repay, liquidation, '
                               'flashloan, swap, admin_init, admin_config, maintenance, etc.')
    p_search.add_argument('--has-amount', action='store_true',
                          help='Only tables with financial amount columns')
    p_search.add_argument('--json', action='store_true', help='Output as JSON')

    # List
    subparsers.add_parser('list', help='List indexed collections')

    args = parser.parse_args()

    if args.command == 'index':
        cmd_index(args)
    elif args.command == 'index-spellbook':
        cmd_index_spellbook(args)
    elif args.command == 'search':
        cmd_search(args)
    elif args.command == 'list':
        cmd_list(args)


if __name__ == '__main__':
    main()
