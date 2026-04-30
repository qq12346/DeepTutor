#!/bin/bash
set -e

export BACKEND_PORT=${PORT:-8080}

echo "============================================"
echo "🚀 Starting DeepTutor Backend (CloudBase)"
echo "============================================"
echo "📌 Listen Port: ${BACKEND_PORT}"

if [ -z "$LLM_API_KEY" ]; then
    echo "⚠️  Warning: LLM_API_KEY not set"
fi

if [ -z "$LLM_MODEL" ]; then
    echo "⚠️  Warning: LLM_MODEL not set"
fi

python -c "
from pathlib import Path
from deeptutor.services.setup import init_user_directories
init_user_directories(Path('/app'))
" 2>/dev/null || echo "⚠️  Directory initialization skipped (will be created on first use)"

exec python -m uvicorn deeptutor.api.main:app --host 0.0.0.0 --port ${BACKEND_PORT}
