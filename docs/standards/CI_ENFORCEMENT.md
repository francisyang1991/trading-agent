# CI Enforcement & Branch Protection

## Overview

The `expand-universe` branch is protected by GitHub branch protection rules to ensure **all changes go through CI validation** before merging. This enforcement is **mandatory** and applies to all agents and all changes.

## Branch Protection Status

✅ **Enabled** - Verified via GitHub API:
- **Required Status Checks**: All CI checks must pass
- **Enforce Admins**: Even admins cannot bypass
- **Force Pushes**: Disabled
- **Branch Deletions**: Disabled

## Required CI Checks

The following checks **MUST** pass before any PR can be merged:

1. ✅ **Lint (ruff)** - Code style and error checking
2. ✅ **Unit Tests (Tier 1)** - Pure logic tests (109 tests)
3. ✅ **Integration Tests (Tier 2)** - Mocked external dependencies (39 tests)
4. ✅ **Bot Tests (Tier 3)** - Discord bot utilities (16 tests)
5. ✅ **CI Gate (All Checks Must Pass)** - Final gate ensuring all upstream jobs passed

## What This Means

### For All Agents

1. **Direct pushes to `expand-universe` are BLOCKED**
   - GitHub will reject any direct push attempt
   - Error: "Cannot force-push to a protected branch"

2. **PRs cannot be merged until CI passes**
   - The merge button is disabled until all checks are green
   - Even if you have admin rights, you cannot bypass this

3. **Force pushes are disabled**
   - Branch history is protected
   - Prevents accidental or malicious history rewrites

4. **No exceptions**
   - This applies to all users, including repository admins
   - There is no way to bypass these protections

### Workflow

```
1. Pull from mainline
   git checkout expand-universe && git pull origin expand-universe

2. Create feature branch
   git checkout -b feature/my-feature

3. Make changes and test locally
   pytest -m unit
   pytest -m integration
   pytest -m bot

4. Push feature branch
   git push origin feature/my-feature

5. Create Pull Request
   gh pr create --base expand-universe --head feature/my-feature

6. Wait for CI to pass
   - All 5 checks must show ✅ green
   - CI Gate will be the last to pass

7. Merge PR (only after CI passes)
   gh pr merge <PR_NUMBER> --merge
```

## Verification

To verify branch protection is active:

```bash
gh api repos/francisyang1991/trading-agent/branches/expand-universe/protection \
  --jq '{required_status_checks: .required_status_checks.contexts, enforce_admins: .enforce_admins.enabled}'
```

Expected output:
```json
{
  "required_status_checks": [
    "Lint (ruff)",
    "Unit Tests (Tier 1)",
    "Integration Tests (Tier 2)",
    "Bot Tests (Tier 3)",
    "CI Gate (All Checks Must Pass)"
  ],
  "enforce_admins": true
}
```

## Updating OpenClaw Rules

To update AWS OpenClaw agent rules with CI enforcement:

```bash
./scripts/update_openclaw_ci_rules.sh
```

This script appends the CI enforcement rules to `~/.openclaw/workspace/AGENTS.md` on the AWS instance.

## Troubleshooting

### "Cannot push to protected branch"
- **Solution**: Create a feature branch and submit a PR instead

### "Required status checks must pass"
- **Solution**: Wait for all CI checks to complete and pass. If checks fail, fix the issues and push again.

### "Branch is protected"
- **Solution**: This is expected behavior. Use PR workflow instead of direct pushes.

## Related Files

- `.github/workflows/ci.yml` - CI pipeline definition
- `pytest.ini` - Test configuration
- `.cursor/rules/trading-agent.md` - Local workspace rules
- `.cursor/rules/candid-planner.mdc` - Development principles
- `~/.openclaw/workspace/AGENTS.md` - AWS OpenClaw agent rules
