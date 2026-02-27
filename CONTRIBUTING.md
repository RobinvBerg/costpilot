# Contributing to KIRA Cost Cockpit

## Development Setup

```bash
git clone <repo>
cd cost-cockpit
python3 server.py
```

## Architecture

- **server.py** — Pure stdlib HTTP server. Add endpoints in `_dispatch_get`/`_dispatch_post`.
- **auto_logger.py** — Delta tracker. Add new input sources by extending `read_sessions_file()`.
- **dashboard.html** — Single-file frontend. All JS is in `<script>` tags at the bottom.
- **config.json** — User config. Always add defaults to `CONFIG_DEFAULTS` in server.py.

## Adding a New API Endpoint

1. Add to `_dispatch_get` or `_dispatch_post` in `Handler`
2. Return JSON via `self._json(data)`
3. Add to `_get_api_docs()` schema
4. Document in `README.md` API Reference table
5. Add smoke test in `test_server.py`

## Adding a New Config Field

1. Add default to `CONFIG_DEFAULTS` in `server.py`
2. Add type to `CONFIG_FIELD_TYPES` for validation
3. Include in `/api/config` POST allowed fields
4. Surface in config modal in `dashboard.html`
5. Add to `config.example.json` with comment
6. Document in `README.md` Configuration table

## Adding a New Dashboard Feature

1. Fetch data from `/api/data` (data is already rich — check first!)
2. Add render function (e.g. `renderMyFeature(data)`)
3. Call it from `render(data)` wrapped in try/catch
4. Use `fmtCost()` for all cost values
5. Use CSS variables for colors (never hardcode)
6. Test on mobile viewport (768px)

## Code Style

- Python: 4-space indent, type hints on new functions
- JavaScript: 2-space indent, const/let (no var)
- CSS: keep in alphabetical order within blocks
- Always handle null/undefined in JS

## Testing

```bash
make test
# or
python3 test_server.py
python3 test_auto_logger.py
```

## Pull Request Checklist

- [ ] `make test` passes
- [ ] New config fields have defaults
- [ ] New endpoints documented in README
- [ ] No hardcoded colors/values
- [ ] Mobile-friendly
