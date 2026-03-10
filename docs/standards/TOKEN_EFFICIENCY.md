# Token Efficiency in Cursor - Best Practices

## How Cursor Uses Files for Context

Cursor **does NOT** load all files into context automatically. Instead, it uses:

1. **Semantic Search**: Only loads files relevant to your current query/task
2. **Rule Files**: `.cursor/rules/*.mdc` files are always loaded (these are your project rules)
3. **Open Files**: Files you have open in the editor
4. **Recently Viewed**: Files you've recently viewed (limited)
5. **Codebase Search**: When you ask questions, Cursor searches and loads only relevant files

## What Actually Saves Tokens

### ✅ Effective Token-Saving Strategies

1. **Use `.cursorignore`** (like `.gitignore`)
   - Exclude large data files, logs, cache, results
   - Exclude virtual environments and build artifacts
   - Example: `data/*.db`, `results/`, `logs/`, `.venv/`

2. **Keep Documentation Concise**
   - Write focused, relevant documentation
   - Avoid duplicating information
   - Use clear file names so semantic search finds the right files

3. **Organize Code Well**
   - Modular code structure helps Cursor find only what's needed
   - Clear naming conventions improve semantic search accuracy
   - Avoid massive files (split into smaller modules)

4. **Use Codebase Search Instead of Reading Entire Files**
   - Ask specific questions: "How does X work?" instead of "Read file Y"
   - Cursor will load only relevant parts

5. **Limit Open Files**
   - Close files you're not actively working on
   - Cursor includes open files in context

6. **Keep Rules Files Focused**
   - `.cursor/rules/*.mdc` files are always loaded
   - Keep them concise and relevant
   - Don't duplicate information already in code/docs

### ❌ Ineffective Token-Saving Strategies

1. **Converting Markdown to QMD**
   - Cursor uses semantic search, not full file reads
   - File format doesn't significantly impact token usage
   - Standard Markdown is more compatible and readable

2. **Minifying Code**
   - Hurts readability and maintainability
   - Cursor's semantic search works better with well-formatted code

3. **Removing Comments**
   - Comments help semantic search understand code context
   - Well-documented code is easier for AI to work with

## Recommended `.cursorignore` Patterns

```gitignore
# Large data files
data/*.db
data/*.sqlite
data/cache/
data/historical/
data/reports/

# Results and logs
results/
logs/
*.log

# Virtual environments
.venv/
venv/
node_modules/

# Build artifacts
dist/
build/
__pycache__/

# Test artifacts
.pytest_cache/
.coverage

# Temporary files
*.tmp
*.bak
scan_results_*.txt
```

## Key Insight

**Cursor is smart about what to load.** Focus on:
- **Organization**: Well-structured code and docs
- **Exclusion**: Use `.cursorignore` for large/unnecessary files
- **Clarity**: Clear naming and documentation helps semantic search

The biggest token savings come from **excluding large data files and build artifacts**, not from changing file formats.
