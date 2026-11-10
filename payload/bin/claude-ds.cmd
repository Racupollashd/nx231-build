@echo off
rem deepseek / claude-ds: Claude Code harness with ALL your memories, skills, hooks and CLAUDE.md, brain = DeepSeek.
rem Extra settings: __HOMEBS__\Tools\DeepSeek\claude-ds.settings.json (permission mode, model picker, status line)
rem Extra instructions: __HOMEBS__\Tools\DeepSeek\deepseek-mode.md
rem Normal "claude" is not affected by anything in this file.
setlocal
for /f "usebackq delims=" %%K in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"`) do set "ANTHROPIC_AUTH_TOKEN=%%K"
if "%ANTHROPIC_AUTH_TOKEN%"=="" (echo DEEPSEEK_API_KEY not set & exit /b 2)
set "ANTHROPIC_API_KEY="
set "ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic"
set "ANTHROPIC_MODEL=sonnet"
set "ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-flash[1m]"
set "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME=DeepSeek Flash"
set "ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro[1m]"
set "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME=DeepSeek V4 Pro"
set "ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-flash[1m]"
set "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME=DeepSeek Flash"
set "ANTHROPIC_DEFAULT_FABLE_MODEL=deepseek-flash[1m]"
set "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME=DeepSeek Flash"
set "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES=effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking"
set "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES=effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking"
set "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES=effort,xhigh_effort,max_effort,thinking,adaptive_thinking,interleaved_thinking"
set "CLAUDE_CODE_SUBAGENT_MODEL=deepseek-flash[1m]"
rem Force Flash on every subagent, even ones spawned with model opus (= V4 Pro, ~4x price).
set "CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1"
set "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
rem Per DeepSeek's official Claude Code guide (api-docs.deepseek.com, checked 2026-11-06): 1M context, compact at 768K.
set "CLAUDE_CODE_AUTO_COMPACT_WINDOW=786432"
rem Effort: guide says max, but A/B test 2026-11-06 (27 runs, balance-checked) showed max = ~20% more cost, same answers. Default high (= what ran before); /effort max when needed.
rem Default effort lives in claude-ds.settings.json (effortLevel high): an env var here would outrank subagent frontmatter and block ultrathink.
set "CLAUDE_CODE_USE_POWERSHELL_TOOL=1"
set "ENABLE_CLAUDEAI_MCP_SERVERS=false"
rem Stop background-agent label summaries (full transcript re-sent every ~30s, invisible to statusline cost). anthropics/claude-code#105981. Side effect: no "fork" subagent type.
set "CLAUDE_CODE_FORK_SUBAGENT=0"
rem DeepSeek rejects inline tool_definition blocks (422) and the session becomes unresumable. anthropics/claude-code#95087
set "CLAUDE_CODE_INLINE_TOOLS=false"
rem Kit installs: pull the latest kit from GitHub before starting (no-op on the source PC, 3 s timeout).
python -I "__HOMEBS__\Tools\DeepSeek\ds_update_check.py" --auto
claude --mcp-config "__HOMEBS__\Tools\DeepSeek\claude-ds.mcp.json" --settings "__HOMEBS__\Tools\DeepSeek\claude-ds.settings.json" --append-system-prompt-file "__HOMEBS__\Tools\DeepSeek\deepseek-mode.md" %*
