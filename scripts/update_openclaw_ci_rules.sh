#!/bin/bash
# Update OpenClaw CI rules on AWS
# This script appends CI enforcement rules to ~/.openclaw/workspace/AGENTS.md

set -e

SSH_KEY="${HOME}/.ssh/openclaw-key.pem"
AWS_HOST="ubuntu@35.90.4.89"
AGENTS_MD="~/.openclaw/workspace/AGENTS.md"

echo "Updating OpenClaw CI rules on AWS..."

ssh -i "$SSH_KEY" "$AWS_HOST" << 'EOF'
cat >> ~/.openclaw/workspace/AGENTS.md << 'ENDRULES'

---

## CI / Multi-Agent Collaboration Workflow (ENFORCED)

### ⚠️ CRITICAL: Branch Protection Enabled
**The `expand-universe` branch is PROTECTED by GitHub branch protection rules.**
- **Direct pushes are BLOCKED** - all changes MUST go through Pull Requests.
- **CI checks are REQUIRED** - the following checks MUST pass before merge:
  - ✅ Lint (ruff)
  - ✅ Unit Tests (Tier 1)
  - ✅ Integration Tests (Tier 2)
  - ✅ Bot Tests (Tier 3)
  - ✅ CI Gate (All Checks Must Pass)
- **No bypassing** - even admins cannot merge without passing CI.
- **No force pushes** - branch history is protected.

**This enforcement applies to ALL agents and ALL changes. There are no exceptions.**

### Golden Rules (MANDATORY)
1. **Always pull from mainline** before starting any new feature:
   ```bash
   git checkout expand-universe && git pull origin expand-universe
   git checkout -b feature/<your-feature>
   ```
2. **All changes go through PRs** - never push directly to `main` or `expand-universe` (enforced by branch protection).
3. **CI must pass** before any PR can be merged (enforced by branch protection - merge button disabled until all checks pass).
4. **Each agent creates its own feature branch** from the latest mainline.
5. **Run local tests before pushing**:
   ```bash
   pytest -m unit       # Tier 1 - fast, pure logic
   pytest -m integration  # Tier 2 - mocked external deps
   pytest -m bot        # Tier 3 - Discord mocks
   ```

### CI Pipeline (GitHub Actions)
- **Trigger**: On every PR to `main` / `expand-universe`, and on push to those branches.
- **Jobs**: Lint (ruff) -> Unit Tests -> Integration Tests -> Bot Tests -> Coverage.
- **Gate**: All jobs must pass before merge is allowed (enforced by branch protection).
- **Config**: `.github/workflows/ci.yml`

### Test Markers
| Marker | Description | Speed |
|--------|------------|-------|
| `unit` | Pure logic, no external deps | Fast (<1 min) |
| `integration` | Mocked external deps | Moderate |
| `bot` | Discord bot utilities (mocked) | Fast |
| `e2e` | Live connections (manual only) | Slow |

ENDRULES

echo "✅ CI rules updated in ~/.openclaw/workspace/AGENTS.md"
EOF

echo "✅ OpenClaw CI rules updated successfully!"
