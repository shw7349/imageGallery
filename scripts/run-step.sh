#!/usr/bin/env bash
# 사용법: ./scripts/run-step.sh 01        (헤드리스 실행)
#        ./scripts/run-step.sh 01 -i     (대화형 실행: 진행 중 질문·수정 가능)
#
# 흐름: 작업 트리 확인 → 프롬프트 실행 → make check → 통과 시 커밋 + 현재 브랜치로 푸시
# (규칙: .claude/CLAUDE.md "작업 규칙". 06·07·08·12·13·14는 푸시 후 diff를 직접 검토할 것)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

step="${1:?단계 번호를 입력하세요 (예: 01)}"
mode="${2:-}"
file="$(ls prompts/"${step}"-*.md 2>/dev/null | head -n1 || true)"
[ -n "$file" ] || { echo "prompts/${step}-*.md 없음"; exit 1; }

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "커밋되지 않은 변경이 있습니다. 먼저 커밋하거나 정리하세요."; exit 1
fi

echo "▶ ${file}"
if [ "$mode" = "-i" ]; then
  claude "$(cat "$file")"
else
  claude -p "$(cat "$file")" \
    --permission-mode acceptEdits \
    --allowedTools "Bash(uv:*)" "Bash(make:*)" "Bash(docker compose:*)" \
                   "Bash(python:*)" "Bash(ls:*)" "Bash(git status)" "Bash(git diff:*)" \
                   "Bash(terraform fmt:*)" "Bash(terraform validate)" \
    --max-turns 80
fi

echo "▶ 검증: make check"
make check

branch="$(git rev-parse --abbrev-ref HEAD)"
name="$(basename "$file" .md)"
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "변경 사항 없음 (에이전트가 이미 커밋했을 수 있음)."
else
  git add -A
  git commit -m "step ${step}: ${name}"
fi

for i in 1 2 3 4; do
  if git push -u origin "$branch"; then break; fi
  [ "$i" = 4 ] && { echo "푸시 실패"; exit 1; }
  sleep $((2 ** i))
done

echo
echo "✅ ${file} 완료 → origin/${branch} 푸시됨"
case "$step" in
  06|07|08|12|13|14) echo "⚠️  이 단계는 사람이 diff를 직접 검토하세요: git show HEAD" ;;
esac
