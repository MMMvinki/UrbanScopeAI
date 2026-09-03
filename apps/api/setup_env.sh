#!/usr/bin/env bash
# 一键复现 apps/api/.venv 可运行环境（Windows 下用 Git Bash 运行；Linux/macOS 同理）。
#
# 背景：requirements.txt 里 litellm==1.96.2 声明 openai<3.0.0，而 pydantic-ai-slim[openai]==2.32.1
# 需要 openai>=3.0.0，二者运行时真实共存但 pip strict resolver 无法单次同解（选型固有死结）。
# 故分三段安装：先装除 litellm 外全部依赖（带出 openai>=3），再 --no-deps 装 litellm（跳过其过期
# openai<3 声明），最后补齐 litellm 被跳过的真实专属依赖。
#
# 用法：bash apps/api/setup_env.sh          用现有/新建 .venv
#       bash apps/api/setup_env.sh --fresh   强制重建 .venv
set -euo pipefail
cd "$(dirname "$0")"        # apps/api

VENV=".venv"
if [ -f "$VENV/Scripts/python" ]; then PY="$VENV/Scripts/python"; else PY="$VENV/bin/python"; fi

if [ "${1:-}" = "--fresh" ]; then
  echo ">> 删除旧 $VENV ..."
  rm -rf "$VENV"
  PY=""
fi
if [ ! -f "$PY" ]; then
  echo ">> 创建 venv 于 $VENV ..."
  python -m venv "$VENV"
  [ -f "$VENV/Scripts/python" ] && PY="$VENV/Scripts/python" || PY="$VENV/bin/python"
fi
"$PY" -m pip install --upgrade pip >/dev/null

# 抽取「除 litellm 外」的依赖作为首批可解算清单（避免 strict resolver 撞 openai 死结）
tmp_req="$(mktemp)"
grep -v -E '^litellm==1\.96\.2' requirements.txt > "$tmp_req"

echo ">> (1/3) 安装基础依赖（含 openai>=3、pydantic-ai-slim、gis-mcp/rasterio、socksio ...）"
"$PY" -m pip install -r "$tmp_req"
rm -f "$tmp_req"

echo ">> (2/3) 安装 litellm（--no-deps 跳过其过期 openai<3 声明，保留已装的 openai>=3）"
"$PY" -m pip install --no-deps litellm==1.96.2

echo ">> (3/3) 补 litellm --no-deps 跳过的真实专属依赖"
"$PY" -m pip install fastuuid python-dotenv tiktoken tokenizers click jinja2 aiohttp importlib-metadata

echo ">> 完成。验证：$PY -c \"import litellm, pydantic_ai, pydantic_ai.models.openai, pydantic_ai.providers.deepseek, rasterio, gis_mcp; print('SETUP-OK')\""
