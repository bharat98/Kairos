# Kairos

Strategic life advisor bot on Telegram that triages tasks against long-term goals, enforces alignment, and runs hourly accountability check-ins.

## Repositories
- Code: https://github.com/bharat98/Kairos
- Docs: https://github.com/bharat98/Kairos-docs

## Local Paths (Examples)
- Windows: `C:\path\to\Kairos`
- WSL: `/path/to/Kairos`
- Docs home: `/path/to/Kairos-docs`

## Run (Windows)
```powershell
cd C:\path\to\Kairos
.\venv\Scripts\Activate.ps1
python -m src.bot
```

## Run (Linux)
```bash
cd ~/kairos
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m src.bot
```

## Required Environment Variables
- `TELEGRAM_BOT_TOKEN`
- `GEMINI_API_KEY`
- `OBSIDIAN_VAULT_PATH` (optional)
- `DB_PATH` (optional; defaults to `kairos.db`)

## Notes
- Runtime files (`.env`, `kairos.db`, `src/data/temp/`) are intentionally ignored by git.
- Detailed functional docs live in the docs repo.
