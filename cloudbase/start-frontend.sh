#!/bin/bash
set -e

FRONTEND_PORT=${PORT:-8080}

if [ -n "$NEXT_PUBLIC_API_BASE_EXTERNAL" ]; then
    API_BASE="$NEXT_PUBLIC_API_BASE_EXTERNAL"
    echo "[Frontend] 📌 Using NEXT_PUBLIC_API_BASE_EXTERNAL=${API_BASE}"
elif [ -n "$NEXT_PUBLIC_API_BASE" ]; then
    API_BASE="$NEXT_PUBLIC_API_BASE"
    echo "[Frontend] 📌 Using NEXT_PUBLIC_API_BASE=${API_BASE}"
else
    API_BASE="http://localhost:8001"
    echo "[Frontend] ⚠️  No external API base provided, fallback to ${API_BASE}"
    echo "[Frontend] ⚠️  CloudBase 双服务部署时建议设置 NEXT_PUBLIC_API_BASE_EXTERNAL=https://你的公共域名"
fi

echo "[Frontend] 🚀 Starting DeepTutor Frontend (CloudBase) on port ${FRONTEND_PORT}"

find /app/web/.next -type f \( -name "*.js" -o -name "*.json" \) -exec \
    sed -i "s|__NEXT_PUBLIC_API_BASE_PLACEHOLDER__|${API_BASE}|g" {} \; 2>/dev/null || true

export PORT=${FRONTEND_PORT}
export HOSTNAME=0.0.0.0
exec node /app/web/server.js
