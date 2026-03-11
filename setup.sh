#!/usr/bin/env bash
set -euo pipefail

REPO="SynthThoughts/dune-query-skill"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data/chroma_db"
TAG="v2.0.0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     Dune Query Skill — Setup           ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
echo

# Check Python dependency
if ! python3 -c "import chromadb" 2>/dev/null; then
    echo -e "${YELLOW}Installing chromadb...${NC}"
    pip install chromadb
fi

# Show mode selection
echo "Select installation mode:"
echo
echo -e "  ${GREEN}1) Lite${NC}      (~50KB)  — No vector index"
echo "     Best for: Dune API key users (MCP handles table discovery)"
echo "     Requires: Dune MCP server configured"
echo
echo -e "  ${GREEN}2) Standard${NC}  (~18MB)  — Local MiniLM embeddings"
echo "     Best for: Offline semantic search, no external API needed"
echo "     Works without any API key"
echo
echo -e "  ${GREEN}3) Full${NC}      (~56MB)  — Local + Gemini embeddings"
echo "     Best for: Highest precision search"
echo "     Requires: Gemini API key or Google Cloud ADC"
echo

# Check if data already exists
if [ -d "$DATA_DIR" ] && [ -f "$DATA_DIR/chroma.sqlite3" ]; then
    EXISTING=$(python3 -c "
import chromadb
c = chromadb.PersistentClient(path='$DATA_DIR')
cols = [col.name if hasattr(col,'name') else str(col) for col in c.list_collections()]
print(','.join(sorted(cols)))
" 2>/dev/null || echo "")
    if [ -n "$EXISTING" ]; then
        echo -e "${YELLOW}Existing index detected: $EXISTING${NC}"
        echo -e "${YELLOW}Re-running setup will replace the existing index.${NC}"
        echo
    fi
fi

read -rp "Choose mode [1/2/3]: " MODE

case "$MODE" in
    1)
        echo
        echo -e "${GREEN}Lite mode — no vector index needed.${NC}"
        echo "SKILL.md + references are ready. Configure Dune MCP for table discovery."
        echo
        echo "  Claude Code:  claude mcp add dune -e DUNE_API_KEY=<key> -- npx -y @duneanalytics/mcp-server"
        echo "  Cursor:       Add Dune MCP to .cursor/mcp.json"
        echo
        exit 0
        ;;
    2)
        ASSET="chroma-standard.tar.gz"
        LABEL="Standard (MiniLM local embeddings)"
        ;;
    3)
        ASSET="chroma-full.tar.gz"
        LABEL="Full (MiniLM + Gemini embeddings)"
        ;;
    *)
        echo -e "${RED}Invalid choice. Exiting.${NC}"
        exit 1
        ;;
esac

echo
echo -e "${CYAN}Downloading $LABEL...${NC}"

# Download from GitHub Release
URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

if command -v curl &>/dev/null; then
    curl -fSL --progress-bar "$URL" -o "$TMPFILE"
elif command -v wget &>/dev/null; then
    wget -q --show-progress "$URL" -O "$TMPFILE"
else
    echo -e "${RED}Error: curl or wget required${NC}"
    exit 1
fi

# Extract
echo -e "${CYAN}Extracting to $DATA_DIR...${NC}"
mkdir -p "$DATA_DIR"
tar -xzf "$TMPFILE" -C "$DATA_DIR"

# Verify
echo
echo -e "${CYAN}Verifying index...${NC}"
python3 -c "
import chromadb
client = chromadb.PersistentClient(path='$DATA_DIR')
for col_obj in client.list_collections():
    name = col_obj.name if hasattr(col_obj, 'name') else str(col_obj)
    col = client.get_collection(name)
    print(f'  {name}: {col.count()} items')
"

echo
echo -e "${GREEN}Setup complete!${NC}"
echo
echo "Test it:"
echo "  python3 scripts/dune_table_indexer.py search \"DEX trading volume\" -c all"
echo
if [ "$MODE" = "3" ]; then
    echo "For Gemini-quality search, ensure one of:"
    echo "  - Google Cloud ADC: gcloud auth application-default login"
    echo "  - Or: export GEMINI_API_KEY=your_key"
    echo
fi
echo "For full query execution, add Dune MCP:"
echo "  claude mcp add dune -e DUNE_API_KEY=<key> -- npx -y @duneanalytics/mcp-server"
